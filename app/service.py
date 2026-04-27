from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .constants import (
    CATEGORY_MACROS,
    COOKING_REQUIREMENTS,
    DISH_CATEGORIES,
    FLAG_KEYS,
    PRODUCT_CATEGORIES,
    PRODUCT_SORT_FIELDS,
)
from .db import get_connection, init_db
from .errors import ConflictError, NotFoundError, ValidationError


class RecipeBookService:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        init_db(self.db_path)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _as_float(value: Any, field_name: str) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise ValidationError(f"Поле '{field_name}' должно быть числом") from exc
        return numeric

    @staticmethod
    def _as_bool(value: Any, field_name: str) -> bool:
        if isinstance(value, bool):
            return value
        raise ValidationError(f"Поле '{field_name}' должно быть булевым")

    @staticmethod
    def _validate_name(value: Any, field_name: str = "name") -> str:
        if not isinstance(value, str):
            raise ValidationError(f"Поле '{field_name}' должно быть строкой")
        stripped = value.strip()
        if len(stripped) < 2:
            raise ValidationError(f"Поле '{field_name}' должно содержать минимум 2 символа")
        return stripped

    @staticmethod
    def _validate_photos(value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValidationError("Поле 'photos' должно быть массивом")
        if len(value) > 5:
            raise ValidationError("Можно указать максимум 5 фотографий")

        photos: list[str] = []
        for idx, item in enumerate(value):
            if not isinstance(item, str):
                raise ValidationError(f"Элемент photos[{idx}] должен быть строкой")
            cleaned = item.strip()
            if not cleaned:
                raise ValidationError(f"Элемент photos[{idx}] не может быть пустым")
            photos.append(cleaned)
        return photos

    def _validate_flags(self, value: Any) -> dict[str, bool]:
        if value is None:
            return {flag: False for flag in FLAG_KEYS}
        if not isinstance(value, dict):
            raise ValidationError("Поле 'flags' должно быть объектом")

        flags = {flag: False for flag in FLAG_KEYS}
        for flag in FLAG_KEYS:
            if flag in value:
                flags[flag] = self._as_bool(value[flag], f"flags.{flag}")
        return flags

    def _sanitize_product_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        required_fields = [
            "name",
            "calories",
            "proteins",
            "fats",
            "carbs",
            "category",
            "cooking_requirement",
        ]
        for field in required_fields:
            if field not in payload:
                raise ValidationError(f"Отсутствует обязательное поле '{field}'")

        name = self._validate_name(payload["name"], "name")
        photos = self._validate_photos(payload.get("photos"))
        composition = payload.get("composition")
        if composition is not None and not isinstance(composition, str):
            raise ValidationError("Поле 'composition' должно быть строкой или null")

        calories = self._as_float(payload["calories"], "calories")
        proteins = self._as_float(payload["proteins"], "proteins")
        fats = self._as_float(payload["fats"], "fats")
        carbs = self._as_float(payload["carbs"], "carbs")

        if calories < 0:
            raise ValidationError("Калорийность не может быть меньше 0")
        for name_field, val in (("proteins", proteins), ("fats", fats), ("carbs", carbs)):
            if val < 0 or val > 100:
                raise ValidationError(f"Поле '{name_field}' должно быть в диапазоне 0..100")
        if proteins + fats + carbs > 100:
            raise ValidationError("Сумма БЖУ продукта не может превышать 100")

        category = payload["category"]
        if category not in PRODUCT_CATEGORIES:
            raise ValidationError("Некорректная категория продукта")

        cooking_requirement = payload["cooking_requirement"]
        if cooking_requirement not in COOKING_REQUIREMENTS:
            raise ValidationError("Некорректное значение поля 'cooking_requirement'")

        flags = self._validate_flags(payload.get("flags"))

        return {
            "name": name,
            "photos": photos,
            "calories": calories,
            "proteins": proteins,
            "fats": fats,
            "carbs": carbs,
            "composition": composition.strip() if isinstance(composition, str) and composition.strip() else None,
            "category": category,
            "cooking_requirement": cooking_requirement,
            "flags": flags,
        }

    @staticmethod
    def _apply_macro_to_name(name: str) -> tuple[str, str | None]:
        lowered = name.lower()
        first_macro: str | None = None
        first_index = -1

        for macro in CATEGORY_MACROS:
            idx = lowered.find(macro)
            if idx == -1:
                continue
            if first_macro is None or idx < first_index:
                first_macro = macro
                first_index = idx

        if first_macro is None:
            return name.strip(), None

        cleaned = (name[:first_index] + name[first_index + len(first_macro) :]).strip()
        cleaned = " ".join(cleaned.split())
        category = CATEGORY_MACROS[first_macro]
        return cleaned, category

    def _normalize_ingredients(
        self,
        conn,
        ingredients_payload: Any,
    ) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
        if not isinstance(ingredients_payload, list):
            raise ValidationError("Поле 'ingredients' должно быть массивом")
        if len(ingredients_payload) < 1:
            raise ValidationError("У блюда должен быть минимум один ингредиент")

        normalized: list[dict[str, Any]] = []
        seen_product_ids: set[int] = set()

        for idx, item in enumerate(ingredients_payload):
            if not isinstance(item, dict):
                raise ValidationError(f"ingredients[{idx}] должен быть объектом")
            if "product_id" not in item or "amount" not in item:
                raise ValidationError(f"ingredients[{idx}] должен содержать 'product_id' и 'amount'")

            try:
                product_id = int(item["product_id"])
            except (TypeError, ValueError) as exc:
                raise ValidationError(f"ingredients[{idx}].product_id должен быть целым") from exc

            if product_id in seen_product_ids:
                raise ValidationError("Один и тот же продукт не должен повторяться в составе блюда")
            seen_product_ids.add(product_id)

            amount = self._as_float(item["amount"], f"ingredients[{idx}].amount")
            if amount <= 0:
                raise ValidationError(f"ingredients[{idx}].amount должен быть > 0")

            normalized.append({"product_id": product_id, "amount": amount})

        placeholders = ",".join("?" for _ in seen_product_ids)
        rows = conn.execute(
            f"SELECT * FROM products WHERE id IN ({placeholders})",
            tuple(seen_product_ids),
        ).fetchall()
        products = {int(row["id"]): self._map_product_row(row) for row in rows}

        missing = sorted(pid for pid in seen_product_ids if pid not in products)
        if missing:
            raise ValidationError(f"Не найдены продукты с id: {missing}")

        return normalized, products

    @staticmethod
    def _calculate_draft_nutrition(
        ingredients: list[dict[str, Any]],
        products_by_id: dict[int, dict[str, Any]],
    ) -> dict[str, float]:
        totals = {"calories": 0.0, "proteins": 0.0, "fats": 0.0, "carbs": 0.0}
        for item in ingredients:
            product = products_by_id[item["product_id"]]
            ratio = item["amount"] / 100.0
            totals["calories"] += product["calories"] * ratio
            totals["proteins"] += product["proteins"] * ratio
            totals["fats"] += product["fats"] * ratio
            totals["carbs"] += product["carbs"] * ratio
        return {key: round(val, 4) for key, val in totals.items()}

    @staticmethod
    def _available_dish_flags(products_by_id: dict[int, dict[str, Any]]) -> dict[str, bool]:
        if not products_by_id:
            return {flag: False for flag in FLAG_KEYS}
        return {
            flag: all(product["flags"][flag] for product in products_by_id.values())
            for flag in FLAG_KEYS
        }

    def _sanitize_dish_payload(
        self,
        conn,
        payload: dict[str, Any],
        previous_flags: dict[str, bool] | None = None,
    ) -> dict[str, Any]:
        required_fields = ["name", "portion_size", "ingredients"]
        for field in required_fields:
            if field not in payload:
                raise ValidationError(f"Отсутствует обязательное поле '{field}'")

        raw_name = self._validate_name(payload["name"], "name")
        name_without_macro, macro_category = self._apply_macro_to_name(raw_name)
        name = self._validate_name(name_without_macro, "name")

        category = payload.get("category")
        if category is None:
            category = macro_category
        if category not in DISH_CATEGORIES:
            raise ValidationError("Некорректная категория блюда")

        portion_size = self._as_float(payload["portion_size"], "portion_size")
        if portion_size <= 0:
            raise ValidationError("Размер порции должен быть > 0")

        photos = self._validate_photos(payload.get("photos"))

        ingredients, products_by_id = self._normalize_ingredients(conn, payload["ingredients"])
        draft = self._calculate_draft_nutrition(ingredients, products_by_id)

        calories = self._as_float(payload.get("calories", draft["calories"]), "calories")
        proteins = self._as_float(payload.get("proteins", draft["proteins"]), "proteins")
        fats = self._as_float(payload.get("fats", draft["fats"]), "fats")
        carbs = self._as_float(payload.get("carbs", draft["carbs"]), "carbs")

        for name_field, val in (("calories", calories), ("proteins", proteins), ("fats", fats), ("carbs", carbs)):
            if val < 0:
                raise ValidationError(f"Поле '{name_field}' не может быть меньше 0")

        bju_per_100 = (proteins + fats + carbs) / portion_size * 100.0
        if bju_per_100 > 100:
            raise ValidationError("Сумма БЖУ блюда на 100 грамм не может превышать 100")

        available_flags = self._available_dish_flags(products_by_id)
        incoming_flags = self._validate_flags(payload.get("flags"))
        if previous_flags:
            for flag in FLAG_KEYS:
                if flag not in payload.get("flags", {}):
                    incoming_flags[flag] = previous_flags.get(flag, False)

        effective_flags = {flag: incoming_flags[flag] and available_flags[flag] for flag in FLAG_KEYS}
        explicitly_requested = payload.get("flags", {}) if isinstance(payload.get("flags"), dict) else {}
        for flag in FLAG_KEYS:
            if explicitly_requested.get(flag) is True and not available_flags[flag]:
                raise ValidationError(f"Флаг '{flag}' недоступен для текущего состава блюда")

        return {
            "name": name,
            "photos": photos,
            "category": category,
            "portion_size": portion_size,
            "ingredients": ingredients,
            "calories": calories,
            "proteins": proteins,
            "fats": fats,
            "carbs": carbs,
            "flags": effective_flags,
            "available_flags": available_flags,
            "draft_nutrition": draft,
        }

    @staticmethod
    def _map_product_row(row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "name": row["name"],
            "photos": json.loads(row["photos"]),
            "calories": row["calories"],
            "proteins": row["proteins"],
            "fats": row["fats"],
            "carbs": row["carbs"],
            "composition": row["composition"],
            "category": row["category"],
            "cooking_requirement": row["cooking_requirement"],
            "flags": {
                "vegan": bool(row["vegan"]),
                "gluten_free": bool(row["gluten_free"]),
                "sugar_free": bool(row["sugar_free"]),
            },
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _base_map_dish_row(row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "name": row["name"],
            "photos": json.loads(row["photos"]),
            "calories": row["calories"],
            "proteins": row["proteins"],
            "fats": row["fats"],
            "carbs": row["carbs"],
            "portion_size": row["portion_size"],
            "category": row["category"],
            "flags": {
                "vegan": bool(row["vegan"]),
                "gluten_free": bool(row["gluten_free"]),
                "sugar_free": bool(row["sugar_free"]),
            },
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def create_product(self, payload: dict[str, Any]) -> dict[str, Any]:
        product = self._sanitize_product_payload(payload)
        now = self._now_iso()

        conn = get_connection(self.db_path)
        try:
            cursor = conn.execute(
                """
                INSERT INTO products (
                    name, photos, calories, proteins, fats, carbs, composition,
                    category, cooking_requirement, vegan, gluten_free, sugar_free,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    product["name"],
                    json.dumps(product["photos"], ensure_ascii=False),
                    product["calories"],
                    product["proteins"],
                    product["fats"],
                    product["carbs"],
                    product["composition"],
                    product["category"],
                    product["cooking_requirement"],
                    int(product["flags"]["vegan"]),
                    int(product["flags"]["gluten_free"]),
                    int(product["flags"]["sugar_free"]),
                    now,
                    None,
                ),
            )
            conn.commit()
            product_id = int(cursor.lastrowid)
        finally:
            conn.close()

        return self.get_product(product_id)

    def list_products(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        filters = filters or {}
        where: list[str] = []
        params: list[Any] = []
        raw_search = filters.get("q")
        search = str(raw_search).strip() if raw_search is not None else ""
        search_casefold = search.casefold() if search else ""

        category = filters.get("category")
        if category:
            if category not in PRODUCT_CATEGORIES:
                raise ValidationError("Некорректная категория продукта")
            where.append("category = ?")
            params.append(category)

        cooking_requirement = filters.get("cooking_requirement")
        if cooking_requirement:
            if cooking_requirement not in COOKING_REQUIREMENTS:
                raise ValidationError("Некорректное значение поля 'cooking_requirement'")
            where.append("cooking_requirement = ?")
            params.append(cooking_requirement)

        for flag in FLAG_KEYS:
            val = filters.get(flag)
            if val is None:
                continue
            if not isinstance(val, bool):
                raise ValidationError(f"Фильтр '{flag}' должен быть булевым")
            where.append(f"{flag} = ?")
            params.append(int(val))

        sort_by = filters.get("sort_by", "name")
        if sort_by not in PRODUCT_SORT_FIELDS:
            raise ValidationError("Некорректное поле сортировки")

        order = str(filters.get("order", "asc")).lower()
        if order not in {"asc", "desc"}:
            raise ValidationError("Параметр order должен быть 'asc' или 'desc'")

        sort_expr = PRODUCT_SORT_FIELDS[sort_by]
        if sort_by == "name":
            order_clause = f"{sort_expr} COLLATE NOCASE {order.upper()}"
        else:
            order_clause = f"{sort_expr} {order.upper()}"

        query = "SELECT * FROM products"
        if where:
            query += " WHERE " + " AND ".join(where)
        query += f" ORDER BY {order_clause}"

        conn = get_connection(self.db_path)
        try:
            rows = conn.execute(query, tuple(params)).fetchall()
        finally:
            conn.close()

        products = [self._map_product_row(row) for row in rows]
        if search_casefold:
            products = [item for item in products if search_casefold in item["name"].casefold()]
        return products

    def get_product(self, product_id: int) -> dict[str, Any]:
        conn = get_connection(self.db_path)
        try:
            row = conn.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        finally:
            conn.close()

        if row is None:
            raise NotFoundError(f"Продукт id={product_id} не найден")
        return self._map_product_row(row)

    def update_product(self, product_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.get_product(product_id)

        merged = {
            "name": payload.get("name", current["name"]),
            "photos": payload.get("photos", current["photos"]),
            "calories": payload.get("calories", current["calories"]),
            "proteins": payload.get("proteins", current["proteins"]),
            "fats": payload.get("fats", current["fats"]),
            "carbs": payload.get("carbs", current["carbs"]),
            "composition": payload.get("composition", current["composition"]),
            "category": payload.get("category", current["category"]),
            "cooking_requirement": payload.get("cooking_requirement", current["cooking_requirement"]),
            "flags": current["flags"],
        }

        if "flags" in payload:
            merged_flags = dict(current["flags"])
            incoming = payload.get("flags")
            if not isinstance(incoming, dict):
                raise ValidationError("Поле 'flags' должно быть объектом")
            for flag in FLAG_KEYS:
                if flag in incoming:
                    merged_flags[flag] = self._as_bool(incoming[flag], f"flags.{flag}")
            merged["flags"] = merged_flags

        product = self._sanitize_product_payload(merged)
        now = self._now_iso()

        conn = get_connection(self.db_path)
        try:
            conn.execute(
                """
                UPDATE products
                SET name = ?, photos = ?, calories = ?, proteins = ?, fats = ?, carbs = ?,
                    composition = ?, category = ?, cooking_requirement = ?,
                    vegan = ?, gluten_free = ?, sugar_free = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    product["name"],
                    json.dumps(product["photos"], ensure_ascii=False),
                    product["calories"],
                    product["proteins"],
                    product["fats"],
                    product["carbs"],
                    product["composition"],
                    product["category"],
                    product["cooking_requirement"],
                    int(product["flags"]["vegan"]),
                    int(product["flags"]["gluten_free"]),
                    int(product["flags"]["sugar_free"]),
                    now,
                    product_id,
                ),
            )
            self._sync_dishes_for_product(conn, product_id)
            conn.commit()
        finally:
            conn.close()

        return self.get_product(product_id)

    def delete_product(self, product_id: int) -> None:
        _ = self.get_product(product_id)

        conn = get_connection(self.db_path)
        try:
            used_in = conn.execute(
                """
                SELECT d.id, d.name
                FROM dish_ingredients di
                JOIN dishes d ON d.id = di.dish_id
                WHERE di.product_id = ?
                ORDER BY d.name COLLATE NOCASE
                """,
                (product_id,),
            ).fetchall()

            if used_in:
                dishes = [{"id": int(row["id"]), "name": row["name"]} for row in used_in]
                raise ConflictError(
                    json.dumps(
                        {
                            "message": "Нельзя удалить продукт: он используется в блюдах",
                            "dishes": dishes,
                        },
                        ensure_ascii=False,
                    )
                )

            conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
            conn.commit()
        finally:
            conn.close()

    def _insert_dish_ingredients(self, conn, dish_id: int, ingredients: list[dict[str, Any]]) -> None:
        conn.executemany(
            "INSERT INTO dish_ingredients(dish_id, product_id, amount) VALUES (?, ?, ?)",
            [(dish_id, item["product_id"], item["amount"]) for item in ingredients],
        )

    def _sync_dishes_for_product(self, conn, product_id: int) -> None:
        dish_rows = conn.execute(
            "SELECT DISTINCT dish_id FROM dish_ingredients WHERE product_id = ?",
            (product_id,),
        ).fetchall()
        dish_ids = [int(row["dish_id"]) for row in dish_rows]
        if not dish_ids:
            return

        now = self._now_iso()
        for dish_id in dish_ids:
            ingredient_rows = conn.execute(
                """
                SELECT
                    di.product_id,
                    di.amount,
                    p.calories, p.proteins, p.fats, p.carbs,
                    p.vegan, p.gluten_free, p.sugar_free
                FROM dish_ingredients di
                JOIN products p ON p.id = di.product_id
                WHERE di.dish_id = ?
                """,
                (dish_id,),
            ).fetchall()

            ingredients: list[dict[str, Any]] = []
            products = {
                int(row["product_id"]): {
                    "calories": float(row["calories"]),
                    "proteins": float(row["proteins"]),
                    "fats": float(row["fats"]),
                    "carbs": float(row["carbs"]),
                    "flags": {
                        "vegan": bool(row["vegan"]),
                        "gluten_free": bool(row["gluten_free"]),
                        "sugar_free": bool(row["sugar_free"]),
                    }
                }
                for row in ingredient_rows
            }
            for row in ingredient_rows:
                ingredients.append({"product_id": int(row["product_id"]), "amount": float(row["amount"])})

            draft = self._calculate_draft_nutrition(ingredients, products)
            recalculated = self._available_dish_flags(products)

            current = conn.execute(
                "SELECT calories, proteins, fats, carbs, vegan, gluten_free, sugar_free FROM dishes WHERE id = ?",
                (dish_id,),
            ).fetchone()
            if current is None:
                continue

            old_flags = {
                "vegan": bool(current["vegan"]),
                "gluten_free": bool(current["gluten_free"]),
                "sugar_free": bool(current["sugar_free"]),
            }
            next_flags = {
                flag: old_flags[flag] and recalculated[flag]
                for flag in FLAG_KEYS
            }
            needs_nutrition_update = any(
                float(current[field]) != float(draft[field])
                for field in ("calories", "proteins", "fats", "carbs")
            )
            needs_flags_update = old_flags != next_flags
            if not (needs_nutrition_update or needs_flags_update):
                continue

            conn.execute(
                """
                UPDATE dishes
                SET calories = ?, proteins = ?, fats = ?, carbs = ?,
                    vegan = ?, gluten_free = ?, sugar_free = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    draft["calories"],
                    draft["proteins"],
                    draft["fats"],
                    draft["carbs"],
                    int(next_flags["vegan"]),
                    int(next_flags["gluten_free"]),
                    int(next_flags["sugar_free"]),
                    now,
                    dish_id,
                ),
            )

    def create_dish(self, payload: dict[str, Any]) -> dict[str, Any]:
        conn = get_connection(self.db_path)
        now = self._now_iso()
        try:
            dish = self._sanitize_dish_payload(conn, payload)
            cursor = conn.execute(
                """
                INSERT INTO dishes (
                    name, photos, calories, proteins, fats, carbs,
                    portion_size, category, vegan, gluten_free, sugar_free,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dish["name"],
                    json.dumps(dish["photos"], ensure_ascii=False),
                    dish["calories"],
                    dish["proteins"],
                    dish["fats"],
                    dish["carbs"],
                    dish["portion_size"],
                    dish["category"],
                    int(dish["flags"]["vegan"]),
                    int(dish["flags"]["gluten_free"]),
                    int(dish["flags"]["sugar_free"]),
                    now,
                    None,
                ),
            )
            dish_id = int(cursor.lastrowid)
            self._insert_dish_ingredients(conn, dish_id, dish["ingredients"])
            conn.commit()
        finally:
            conn.close()

        return self.get_dish(dish_id)

    def _fetch_dish_row(self, conn, dish_id: int):
        row = conn.execute("SELECT * FROM dishes WHERE id = ?", (dish_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"Блюдо id={dish_id} не найдено")
        return row

    def get_dish(self, dish_id: int) -> dict[str, Any]:
        conn = get_connection(self.db_path)
        try:
            row = self._fetch_dish_row(conn, dish_id)
            dish = self._base_map_dish_row(row)

            ingredients_rows = conn.execute(
                """
                SELECT di.product_id, di.amount, p.name AS product_name
                FROM dish_ingredients di
                JOIN products p ON p.id = di.product_id
                WHERE di.dish_id = ?
                ORDER BY p.name COLLATE NOCASE
                """,
                (dish_id,),
            ).fetchall()
            ingredients = [
                {
                    "product_id": int(item["product_id"]),
                    "product_name": item["product_name"],
                    "amount": item["amount"],
                }
                for item in ingredients_rows
            ]
            dish["ingredients"] = ingredients

            products_for_flags = conn.execute(
                """
                SELECT p.id, p.vegan, p.gluten_free, p.sugar_free
                FROM dish_ingredients di
                JOIN products p ON p.id = di.product_id
                WHERE di.dish_id = ?
                """,
                (dish_id,),
            ).fetchall()
            products = {
                int(item["id"]): {
                    "flags": {
                        "vegan": bool(item["vegan"]),
                        "gluten_free": bool(item["gluten_free"]),
                        "sugar_free": bool(item["sugar_free"]),
                    }
                }
                for item in products_for_flags
            }
            dish["available_flags"] = self._available_dish_flags(products)
        finally:
            conn.close()

        return dish

    def list_dishes(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        filters = filters or {}
        where: list[str] = []
        params: list[Any] = []
        raw_search = filters.get("q")
        search = str(raw_search).strip() if raw_search is not None else ""
        search_casefold = search.casefold() if search else ""

        category = filters.get("category")
        if category:
            if category not in DISH_CATEGORIES:
                raise ValidationError("Некорректная категория блюда")
            where.append("category = ?")
            params.append(category)

        for flag in FLAG_KEYS:
            val = filters.get(flag)
            if val is None:
                continue
            if not isinstance(val, bool):
                raise ValidationError(f"Фильтр '{flag}' должен быть булевым")
            where.append(f"{flag} = ?")
            params.append(int(val))

        query = "SELECT * FROM dishes"
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY name COLLATE NOCASE ASC"

        conn = get_connection(self.db_path)
        try:
            rows = conn.execute(query, tuple(params)).fetchall()
            dishes = [self._base_map_dish_row(row) for row in rows]

            dish_ids = [dish["id"] for dish in dishes]
            ingredients_map: dict[int, list[dict[str, Any]]] = {dish_id: [] for dish_id in dish_ids}
            if dish_ids:
                placeholders = ",".join("?" for _ in dish_ids)
                ingredients_rows = conn.execute(
                    f"""
                    SELECT di.dish_id, di.product_id, di.amount, p.name AS product_name
                    FROM dish_ingredients di
                    JOIN products p ON p.id = di.product_id
                    WHERE di.dish_id IN ({placeholders})
                    ORDER BY p.name COLLATE NOCASE
                    """,
                    tuple(dish_ids),
                ).fetchall()
                for item in ingredients_rows:
                    ingredients_map[int(item["dish_id"])].append(
                        {
                            "product_id": int(item["product_id"]),
                            "product_name": item["product_name"],
                            "amount": item["amount"],
                        }
                    )

            for dish in dishes:
                dish["ingredients"] = ingredients_map[dish["id"]]
        finally:
            conn.close()

        if search_casefold:
            dishes = [item for item in dishes if search_casefold in item["name"].casefold()]
        return dishes

    def update_dish(self, dish_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        current = self.get_dish(dish_id)
        ingredients_updated = "ingredients" in payload

        merged = {
            "name": payload.get("name", current["name"]),
            "photos": payload.get("photos", current["photos"]),
            "portion_size": payload.get("portion_size", current["portion_size"]),
            "category": payload.get("category", current["category"]),
            "ingredients": payload.get(
                "ingredients",
                [{"product_id": i["product_id"], "amount": i["amount"]} for i in current["ingredients"]],
            ),
        }
        for field in ("calories", "proteins", "fats", "carbs"):
            if field in payload:
                merged[field] = payload[field]
            elif not ingredients_updated:
                merged[field] = current[field]

        if "flags" in payload:
            incoming_flags = payload["flags"]
            if not isinstance(incoming_flags, dict):
                raise ValidationError("Поле 'flags' должно быть объектом")
            merged_flags = dict(current["flags"])
            for flag in FLAG_KEYS:
                if flag in incoming_flags:
                    merged_flags[flag] = self._as_bool(incoming_flags[flag], f"flags.{flag}")
            merged["flags"] = merged_flags

        conn = get_connection(self.db_path)
        now = self._now_iso()
        try:
            self._fetch_dish_row(conn, dish_id)
            dish = self._sanitize_dish_payload(conn, merged, previous_flags=current["flags"])

            conn.execute(
                """
                UPDATE dishes
                SET name = ?, photos = ?, calories = ?, proteins = ?, fats = ?, carbs = ?,
                    portion_size = ?, category = ?, vegan = ?, gluten_free = ?, sugar_free = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    dish["name"],
                    json.dumps(dish["photos"], ensure_ascii=False),
                    dish["calories"],
                    dish["proteins"],
                    dish["fats"],
                    dish["carbs"],
                    dish["portion_size"],
                    dish["category"],
                    int(dish["flags"]["vegan"]),
                    int(dish["flags"]["gluten_free"]),
                    int(dish["flags"]["sugar_free"]),
                    now,
                    dish_id,
                ),
            )

            conn.execute("DELETE FROM dish_ingredients WHERE dish_id = ?", (dish_id,))
            self._insert_dish_ingredients(conn, dish_id, dish["ingredients"])
            conn.commit()
        finally:
            conn.close()

        return self.get_dish(dish_id)

    def delete_dish(self, dish_id: int) -> None:
        conn = get_connection(self.db_path)
        try:
            self._fetch_dish_row(conn, dish_id)
            conn.execute("DELETE FROM dishes WHERE id = ?", (dish_id,))
            conn.commit()
        finally:
            conn.close()
