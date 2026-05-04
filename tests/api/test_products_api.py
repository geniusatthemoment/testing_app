"""Интеграционные тесты endpoints продуктов на живом backend."""

from __future__ import annotations

import pytest


class TestProductEndpoints:
    def test_product_crud(self, api_request, create_product_api):
        created = create_product_api(name="Картофель", calories=77.0)

        get_response = api_request("GET", f"/api/products/{created['id']}")
        fetched = get_response.body

        update_response = api_request(
            "PUT",
            f"/api/products/{created['id']}",
            json_payload={"name": f"{created['name']}_новый"},
        )
        updated = update_response.body

        delete_response = api_request("DELETE", f"/api/products/{created['id']}")
        not_found_response = api_request("GET", f"/api/products/{created['id']}")

        assert (
            get_response.status,
            fetched.get("name"),
            update_response.status,
            updated.get("name"),
            updated.get("calories"),
            delete_response.status,
            not_found_response.status,
        ) == (
            200,
            created["name"],
            200,
            f"{created['name']}_новый",
            pytest.approx(77.0),
            204,
            404,
        )

    @pytest.mark.parametrize(
        "sort_by, order, expected_first_suffix",
        [
            ("name", "asc", "Ананас"),
            ("name", "desc", "Яблоко"),
            ("calories", "asc", "Вода"),
            ("proteins", "desc", "Огурец"),
            ("fats", "desc", "Яблоко"),
            ("carbs", "desc", "Яблоко"),
        ],
        ids=[
            "sort_name_asc",
            "sort_name_desc",
            "sort_calories_asc",
            "sort_proteins_desc",
            "sort_fats_desc",
            "sort_carbs_desc",
        ],
    )
    def test_products_list_search_filters_sort(
        self,
        api_request,
        create_product_api,
        name_prefix,
        sort_by,
        order,
        expected_first_suffix,
    ):
        create_product_api(
            name="Яблоко",
            calories=52,
            proteins=0.3,
            fats=0.2,
            carbs=14,
            category="Овощи",
            cooking_requirement="Готовый к употреблению",
            flags={"vegan": True, "gluten_free": True, "sugar_free": False},
        )
        create_product_api(
            name="Вода",
            calories=0,
            proteins=0,
            fats=0,
            carbs=0,
            category="Жидкость",
            cooking_requirement="Готовый к употреблению",
            flags={"vegan": True, "gluten_free": True, "sugar_free": True},
        )
        create_product_api(
            name="Огурец",
            calories=15,
            proteins=0.8,
            fats=0.1,
            carbs=2.8,
            category="Овощи",
            cooking_requirement="Требует приготовления",
            flags={"vegan": True, "gluten_free": True, "sugar_free": True},
        )
        create_product_api(
            name="Ананас",
            calories=50,
            proteins=0.1,
            fats=0.1,
            carbs=13,
            category="Овощи",
            cooking_requirement="Готовый к употреблению",
            flags={"vegan": True, "gluten_free": True, "sugar_free": True},
        )

        sorted_response = api_request(
            "GET",
            "/api/products",
            query={"sort_by": sort_by, "order": order, "q": name_prefix},
        )
        sorted_items = sorted_response.body

        search_response = api_request("GET", "/api/products", query={"q": f"{name_prefix}_Вод"})
        search_items = search_response.body

        filter_response = api_request(
            "GET",
            "/api/products",
            query={
                "q": name_prefix,
                "category": "Овощи",
                "cooking_requirement": "Требует приготовления",
                "vegan": "true",
                "gluten_free": "1",
                "sugar_free": "yes",
            },
        )
        filtered = filter_response.body

        assert (
            sorted_response.status,
            str(sorted_items[0]["name"]).endswith(f"_{expected_first_suffix}"),
            search_response.status,
            len(search_items),
            str(search_items[0]["name"]).endswith("_Вода") if search_items else False,
            filter_response.status,
            len(filtered),
            str(filtered[0]["name"]).endswith("_Огурец") if filtered else False,
        ) == (200, True, 200, 1, True, 200, 1, True)

    def test_products_list_rejects_invalid_boolean_filter(self, api_request, create_product_api):
        create_product_api(name="Тест", calories=1)
        response = api_request("GET", "/api/products", query={"vegan": "maybe"})

        assert (response.status, "булево" in str(response.body.get("error", "")).lower()) == (400, True)

    def test_delete_product_returns_conflict_when_used_in_dish(
        self,
        api_request,
        create_product_api,
        create_dish_api,
    ):
        meat = create_product_api(
            name="Мясо",
            calories=187.2,
            category="Мясной",
            cooking_requirement="Требует приготовления",
            flags={"vegan": False, "gluten_free": True, "sugar_free": True},
        )

        create_dish_api(name="Суп с мясом", ingredients=[{"product_id": meat["id"], "amount": 100.0}])

        response = api_request("DELETE", f"/api/products/{meat['id']}")
        payload = response.body

        assert (
            response.status,
            "message" in payload,
            len(payload.get("dishes", [])) >= 1,
        ) == (409, True, True)

    @pytest.mark.parametrize(
        "field, value",
        [("proteins", -0.1), ("fats", -1.0), ("carbs", -5.0)],
        ids=["negative_proteins", "negative_fats", "negative_carbs"],
    )
    def test_create_product_rejects_negative_macros(
        self,
        api_request,
        product_payload_template,
        name_prefix,
        field,
        value,
    ):
        payload = dict(product_payload_template)
        payload.update({"name": f"{name_prefix}_Невалидный", "calories": 10.0, field: value})

        response = api_request("POST", "/api/products", json_payload=payload)

        assert (response.status, "диапазоне 0..100" in str(response.body.get("error", ""))) == (400, True)

    def test_create_product_rejects_bju_sum_above_100(self, api_request, product_payload_template, name_prefix):
        payload = dict(product_payload_template)
        payload.update(
            {
                "name": f"{name_prefix}_Перебор",
                "calories": 10.0,
                "proteins": 50.0,
                "fats": 30.0,
                "carbs": 20.1,
            }
        )

        response = api_request("POST", "/api/products", json_payload=payload)

        assert (response.status, "сумма бжу" in str(response.body.get("error", "")).lower()) == (400, True)

    def test_create_product_accepts_bju_sum_equal_100(self, api_request, product_payload_template, name_prefix, cleanup_registry):
        payload = dict(product_payload_template)
        payload.update(
            {
                "name": f"{name_prefix}_ГраницаБЖУ",
                "calories": 10.0,
                "proteins": 30.0,
                "fats": 30.0,
                "carbs": 40.0,
            }
        )

        response = api_request("POST", "/api/products", json_payload=payload)
        if response.status == 201:
            cleanup_registry["product_ids"].append(int(response.body["id"]))

        assert response.status == 201

    def test_update_product_returns_404_for_unknown_id(self, api_request):
        response = api_request(
            "PUT",
            "/api/products/999999999",
            json_payload={"name": "Не существует"},
        )

        assert response.status == 404

    def test_delete_product_returns_404_for_unknown_id(self, api_request):
        response = api_request("DELETE", "/api/products/999999999")

        assert response.status == 404
