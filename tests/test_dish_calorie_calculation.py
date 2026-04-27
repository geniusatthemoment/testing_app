"""Юнит-тесты для автоматического расчета калорийности блюда.

Использованные техники тест-дизайна:
- эквивалентное разбиение (типовые валидные классы состава ингредиентов);
- анализ граничных значений (минимум > 0, ноль/отрицательные как невалидные, очень большое значение).
"""

import pytest

from app.errors import ValidationError
from app.service import RecipeBookService


@pytest.fixture()
def calc_service(tmp_path):
    """Изолированный экземпляр сервиса с временной SQLite-базой."""
    return RecipeBookService(tmp_path / "dish_calories.db")


@pytest.fixture()
def ingredient_products(calc_service):
    """Опорные продукты с детерминированной калорийностью для проверки формулы."""

    def make_product(name: str, calories: float) -> dict:
        return calc_service.create_product(
            {
                "name": name,
                "photos": [],
                "calories": calories,
                "proteins": 0,
                "fats": 0,
                "carbs": 0,
                "composition": None,
                "category": "Овощи",
                "cooking_requirement": "Готовый к употреблению",
                "flags": {"vegan": True, "gluten_free": True, "sugar_free": True},
            }
        )

    return {
        "p100": make_product("P100", 100.0),
        "p40": make_product("P40", 40.0),
        "p0": make_product("P0", 0.0),
    }


def _create_dish(calc_service, *, ingredients, name="Тест блюдо", portion_size=300.0, extra=None):
    payload = {
        "name": name,
        "portion_size": portion_size,
        "category": "Суп",
        "ingredients": ingredients,
        "photos": [],
    }
    if extra:
        payload.update(extra)
    return calc_service.create_dish(payload)


class TestDishCaloriesEquivalencePartitions:
    """Эквивалентные классы для автоподсчета калорийности (валидные входы)."""

    @pytest.mark.parametrize(
        "items, expected_calories",
        [
            ([("p100", 100.0)], 100.0),
            ([("p100", 150.0), ("p40", 50.0)], 170.0),
            ([("p0", 500.0), ("p40", 100.0)], 40.0),
            ([("p100", 12.5), ("p40", 33.3)], 25.82),
        ],
        ids=[
            "single_ingredient_typical",
            "multiple_ingredients_typical",
            "includes_zero_calorie_ingredient",
            "fractional_amounts",
        ],
    )
    def test_auto_calories_on_create(self, calc_service, ingredient_products, items, expected_calories):
        ingredients = [
            {"product_id": ingredient_products[key]["id"], "amount": amount}
            for key, amount in items
        ]

        dish = _create_dish(calc_service, ingredients=ingredients)

        assert dish["calories"] == pytest.approx(expected_calories, rel=1e-9, abs=1e-9)


class TestDishCaloriesBoundaryValues:
    """Проверки граничных значений количества ингредиента для автоподсчета калорий."""

    def test_min_positive_amount_boundary(self, calc_service, ingredient_products):
        dish = _create_dish(
            calc_service,
            ingredients=[
                {"product_id": ingredient_products["p100"]["id"], "amount": 0.0001},
            ],
        )

        assert dish["calories"] == pytest.approx(0.0001, rel=1e-12, abs=1e-12)

    @pytest.mark.parametrize("invalid_amount", [0.0, -0.0001], ids=["zero_amount", "negative_amount"])
    def test_invalid_non_positive_amounts_are_rejected(self, calc_service, ingredient_products, invalid_amount):
        with pytest.raises(ValidationError):
            _create_dish(
                calc_service,
                ingredients=[
                    {"product_id": ingredient_products["p100"]["id"], "amount": invalid_amount},
                ],
            )

    def test_large_amount_boundary(self, calc_service, ingredient_products):
        dish = _create_dish(
            calc_service,
            ingredients=[
                {"product_id": ingredient_products["p100"]["id"], "amount": 10000.0},
            ],
        )

        assert dish["calories"] == pytest.approx(10000.0, rel=1e-9, abs=1e-9)


class TestDishCaloriesRecalculation:
    """Проверки пересчета после изменения состава и поведения ручного переопределения."""

    def test_auto_recalculation_on_dish_update(self, calc_service, ingredient_products):
        dish = _create_dish(
            calc_service,
            ingredients=[
                {"product_id": ingredient_products["p40"]["id"], "amount": 100.0},
            ],
            name="Блюдо 1",
        )
        assert dish["calories"] == pytest.approx(40.0)

        updated = calc_service.update_dish(
            dish["id"],
            {
                "ingredients": [
                    {"product_id": ingredient_products["p100"]["id"], "amount": 250.0},
                ],
            },
        )

        assert updated["calories"] == pytest.approx(250.0)

    def test_manual_calorie_override_is_kept(self, calc_service, ingredient_products):
        dish = _create_dish(
            calc_service,
            ingredients=[
                {"product_id": ingredient_products["p40"]["id"], "amount": 100.0},
            ],
            name="Блюдо 2",
            extra={"calories": 999.0},
        )

        assert dish["calories"] == pytest.approx(999.0)

    def test_manual_calorie_override_survives_non_ingredient_update(self, calc_service, ingredient_products):
        dish = _create_dish(
            calc_service,
            ingredients=[
                {"product_id": ingredient_products["p40"]["id"], "amount": 100.0},
            ],
            name="Блюдо 3",
            extra={"calories": 777.0},
        )

        renamed = calc_service.update_dish(dish["id"], {"name": "Блюдо 3 (новое имя)"})

        assert renamed["calories"] == pytest.approx(777.0)
