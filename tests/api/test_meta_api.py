"""Интеграционные тесты endpoint `GET /api/meta` на живом backend."""


class TestMetaEndpoint:
    def test_meta_returns_reference_values(self, api_request):
        response = api_request("GET", "/api/meta")
        payload = response.body

        assert (
            response.status,
            "product_categories" in payload,
            "dish_categories" in payload,
            "flags" in payload,
            "Овощи" in payload.get("product_categories", []),
            "Суп" in payload.get("dish_categories", []),
        ) == (200, True, True, True, True, True)
