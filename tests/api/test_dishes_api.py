"""Интеграционные тесты endpoints блюд и автоподсчета калорий на backend."""

from __future__ import annotations

import pytest


class TestDishCaloriesApiEquivalencePartitions:
    @pytest.mark.parametrize(
        "items, expected_calories",
        [
            (["p100", 100.0, None], 100.0),
            (["p100", 150.0, "p40", 50.0], 170.0),
            (["p0", 500.0, "p40", 100.0], 40.0),
            (["p100", 12.5, "p40", 33.3], 25.82),
        ],
        ids=[
            "single_ingredient_typical",
            "multiple_ingredients_typical",
            "includes_zero_calorie_ingredient",
            "fractional_amounts",
        ],
    )
    def test_auto_calories_on_create(self, create_product_api, create_dish_api, items, expected_calories):
        p100 = create_product_api(name="P100", calories=100.0)
        p40 = create_product_api(name="P40", calories=40.0)
        p0 = create_product_api(name="P0", calories=0.0)

        def _id(code: str) -> int:
            return {"p100": p100["id"], "p40": p40["id"], "p0": p0["id"]}[code]

        ingredients = [{"product_id": _id(items[0]), "amount": items[1]}]
        if items[2] is not None:
            ingredients.append({"product_id": _id(items[2]), "amount": items[3]})

        dish = create_dish_api(name="Тест блюдо", ingredients=ingredients)

        assert dish["calories"] == pytest.approx(expected_calories, rel=1e-9, abs=1e-9)


class TestDishCaloriesApiBoundaryValues:
    def test_min_positive_amount_boundary(self, create_product_api, create_dish_api):
        p100 = create_product_api(name="P100", calories=100.0)
        dish = create_dish_api(name="Минимум", ingredients=[{"product_id": p100["id"], "amount": 0.0001}])

        assert dish["calories"] == pytest.approx(0.0001, rel=1e-12, abs=1e-12)

    @pytest.mark.parametrize(
        "invalid_amount",
        [0.0, -0.0001],
        ids=["zero_amount", "negative_boundary_amount"],
    )
    def test_invalid_non_positive_amounts_are_rejected(self, create_product_api, create_dish_api, invalid_amount):
        p100 = create_product_api(name="P100", calories=100.0)
        response = create_dish_api(
            name="Невалидное",
            ingredients=[{"product_id": p100["id"], "amount": invalid_amount}],
            expected_status=400,
        )

        assert (response.status, "> 0" in str(response.body.get("error", ""))) == (400, True)

    def test_large_amount_from_valid_equivalence_class(self, create_product_api, create_dish_api):
        p100 = create_product_api(name="P100", calories=100.0)
        dish = create_dish_api(name="Большое", ingredients=[{"product_id": p100["id"], "amount": 100000.0}])

        assert dish["calories"] == pytest.approx(100000.0, rel=1e-9, abs=1e-9)


class TestDishEndpoints:
    def test_dish_crud_and_recalculation(self, api_request, create_product_api, create_dish_api):
        potato = create_product_api(name="Картофель", calories=77.0)
        water = create_product_api(name="Вода", calories=0.0, category="Жидкость")

        created = create_dish_api(
            name="Борщ",
            ingredients=[
                {"product_id": potato["id"], "amount": 100.0},
                {"product_id": water["id"], "amount": 100.0},
            ],
            category="Суп",
            flags={"vegan": True, "gluten_free": True, "sugar_free": True},
        )

        get_response = api_request("GET", f"/api/dishes/{created['id']}")
        fetched = get_response.body

        update_response = api_request(
            "PUT",
            f"/api/dishes/{created['id']}",
            json_payload={"ingredients": [{"product_id": potato["id"], "amount": 250.0}]},
        )
        updated = update_response.body

        delete_response = api_request("DELETE", f"/api/dishes/{created['id']}")
        not_found_response = api_request("GET", f"/api/dishes/{created['id']}")

        assert (
            created.get("calories"),
            get_response.status,
            fetched.get("name"),
            len(fetched.get("ingredients", [])),
            update_response.status,
            updated.get("calories"),
            delete_response.status,
            not_found_response.status,
        ) == (pytest.approx(77.0), 200, created["name"], 2, 200, pytest.approx(192.5), 204, 404)

    def test_dishes_list_filters_and_search(self, api_request, create_product_api, create_dish_api, product_payload_template, name_prefix):
        vegan_product = create_product_api(name="Свекла", calories=43.0)

        non_vegan_template = dict(product_payload_template)
        non_vegan_template["flags"] = {"vegan": False, "gluten_free": True, "sugar_free": True}
        meat_product = create_product_api(
            name="Мясо",
            calories=187.2,
            template=non_vegan_template,
            category="Мясной",
            cooking_requirement="Требует приготовления",
        )

        create_dish_api(
            name="Борщ веганский",
            ingredients=[{"product_id": vegan_product["id"], "amount": 100.0}],
            category="Суп",
            flags={"vegan": True, "gluten_free": True, "sugar_free": True},
        )
        create_dish_api(
            name="Борщ мясной",
            ingredients=[{"product_id": meat_product["id"], "amount": 100.0}],
            category="Суп",
            flags={"vegan": False, "gluten_free": True, "sugar_free": True},
        )

        filtered_response = api_request(
            "GET",
            "/api/dishes",
            query={"q": name_prefix, "category": "Суп", "vegan": "1", "gluten_free": "true", "sugar_free": "yes"},
        )
        filtered = filtered_response.body

        search_response = api_request("GET", "/api/dishes", query={"q": f"{name_prefix}_Борщ мяс"})
        search = search_response.body

        assert (
            filtered_response.status,
            len(filtered),
            str(filtered[0]["name"]).endswith("_Борщ веганский") if filtered else False,
            search_response.status,
            len(search),
            str(search[0]["name"]).endswith("_Борщ мясной") if search else False,
        ) == (200, 1, True, 200, 1, True)

    def test_auto_recalculation_on_dish_update(self, api_request, create_product_api, create_dish_api):
        p40 = create_product_api(name="P40", calories=40.0)
        p100 = create_product_api(name="P100", calories=100.0)

        dish = create_dish_api(name="Блюдо 1", ingredients=[{"product_id": p40["id"], "amount": 100.0}])

        updated_response = api_request(
            "PUT",
            f"/api/dishes/{dish['id']}",
            json_payload={"ingredients": [{"product_id": p100["id"], "amount": 250.0}]},
        )
        updated = updated_response.body

        assert (
            dish.get("calories"),
            updated_response.status,
            updated.get("calories"),
        ) == (pytest.approx(40.0), 200, pytest.approx(250.0))

    @pytest.mark.parametrize("invalid_amount", [0.0, -1.0], ids=["zero_amount", "negative_amount"])
    def test_create_dish_rejects_non_positive_ingredient_amount(
        self,
        api_request,
        create_product_api,
        name_prefix,
        invalid_amount,
    ):
        potato = create_product_api(name="Картофель", calories=77.0)
        response = api_request(
            "POST",
            "/api/dishes",
            json_payload={
                "name": f"{name_prefix}_Плохое блюдо",
                "portion_size": 300.0,
                "category": "Суп",
                "ingredients": [{"product_id": potato["id"], "amount": invalid_amount}],
                "photos": [],
            },
        )

        assert response.status == 400

    def test_create_dish_rejects_duplicate_products_in_ingredients(self, api_request, create_product_api, name_prefix):
        potato = create_product_api(name="Картофель", calories=77.0)
        response = api_request(
            "POST",
            "/api/dishes",
            json_payload={
                "name": f"{name_prefix}_Плохое блюдо",
                "portion_size": 300.0,
                "category": "Суп",
                "ingredients": [
                    {"product_id": potato["id"], "amount": 100.0},
                    {"product_id": potato["id"], "amount": 50.0},
                ],
                "photos": [],
            },
        )

        assert (response.status, "не должен повторяться" in str(response.body.get("error", ""))) == (400, True)

    def test_create_dish_rejects_unknown_product(self, api_request, name_prefix):
        response = api_request(
            "POST",
            "/api/dishes",
            json_payload={
                "name": f"{name_prefix}_Плохое блюдо",
                "portion_size": 300.0,
                "category": "Суп",
                "ingredients": [{"product_id": 999999999, "amount": 100.0}],
                "photos": [],
            },
        )

        assert (response.status, "не найдены продукты" in str(response.body.get("error", "")).lower()) == (400, True)

    def test_create_dish_rejects_unavailable_flag(self, api_request, create_product_api, product_payload_template, name_prefix):
        meat_template = dict(product_payload_template)
        meat_template["flags"] = {"vegan": False, "gluten_free": True, "sugar_free": True}
        meat = create_product_api(
            name="Мясо",
            calories=187.2,
            template=meat_template,
            category="Мясной",
            cooking_requirement="Требует приготовления",
        )
        response = api_request(
            "POST",
            "/api/dishes",
            json_payload={
                "name": f"{name_prefix}_Суп с мясом",
                "portion_size": 300.0,
                "category": "Суп",
                "ingredients": [{"product_id": meat["id"], "amount": 100.0}],
                "photos": [],
                "flags": {"vegan": True},
            },
        )

        assert (response.status, "недоступен" in str(response.body.get("error", ""))) == (400, True)

    def test_dish_flag_auto_drops_after_product_flag_update(self, api_request, create_product_api, create_dish_api):
        water = create_product_api(name="Вода", calories=0.0, category="Жидкость")
        beet = create_product_api(name="Свекла", calories=43.0)
        dish = create_dish_api(
            name="Борщ веганский",
            ingredients=[
                {"product_id": water["id"], "amount": 200.0},
                {"product_id": beet["id"], "amount": 100.0},
            ],
            category="Суп",
            flags={"vegan": True, "gluten_free": True, "sugar_free": True},
        )

        update_product_response = api_request(
            "PUT",
            f"/api/products/{water['id']}",
            json_payload={"flags": {"gluten_free": False}},
        )

        dish_response = api_request("GET", f"/api/dishes/{dish['id']}")
        updated_dish = dish_response.body

        assert (
            dish.get("flags", {}).get("gluten_free"),
            update_product_response.status,
            dish_response.status,
            updated_dish.get("flags", {}).get("gluten_free"),
            updated_dish.get("available_flags", {}).get("gluten_free"),
        ) == (True, 200, 200, False, False)

    def test_update_dish_returns_404_for_unknown_id(self, api_request):
        response = api_request(
            "PUT",
            "/api/dishes/999999999",
            json_payload={"name": "Не существует"},
        )

        assert response.status == 404

    def test_delete_dish_returns_404_for_unknown_id(self, api_request):
        response = api_request("DELETE", "/api/dishes/999999999")

        assert response.status == 404

    @pytest.mark.parametrize("category", ["Суп", "Напиток"], ids=["soup", "drink"])
    def test_dishes_filter_by_category(self, api_request, create_product_api, create_dish_api, name_prefix, category):
        product = create_product_api(name="Основа", calories=10.0)
        create_dish_api(name="Суп тест", category="Суп", ingredients=[{"product_id": product["id"], "amount": 100.0}])
        create_dish_api(
            name="Напиток тест",
            category="Напиток",
            ingredients=[{"product_id": product["id"], "amount": 100.0}],
        )

        response = api_request("GET", "/api/dishes", query={"q": name_prefix, "category": category})
        names = [item["name"] for item in response.body]
        expected_names = [f"{name_prefix}_Суп тест"] if category == "Суп" else [f"{name_prefix}_Напиток тест"]

        assert (response.status, names) == (200, expected_names)

    def test_dishes_list_rejects_invalid_boolean_filter(self, api_request, create_product_api, create_dish_api, name_prefix):
        product = create_product_api(name="Основа", calories=10.0)
        create_dish_api(name="Тест блюдо", category="Суп", ingredients=[{"product_id": product["id"], "amount": 100.0}])

        response = api_request("GET", "/api/dishes", query={"q": name_prefix, "vegan": "zzz"})

        assert response.status == 400
