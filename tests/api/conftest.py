"""Общие фикстуры для интеграционных тестов на живом backend API.

Тесты выполняют реальные HTTP-запросы к запущенному серверу
(по умолчанию http://127.0.0.1:8080) и не изолируют backend-логику.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pytest


@dataclass
class HttpResponse:
    status: int
    body: Any
    text: str
    headers: dict[str, str]


def _decode_body(raw: bytes) -> tuple[Any, str]:
    text = raw.decode("utf-8") if raw else ""
    if not text:
        return {}, ""
    try:
        return json.loads(text), text
    except json.JSONDecodeError:
        return {"raw": text}, text


@pytest.fixture(scope="session")
def base_url() -> str:
    return os.getenv("TEST_BASE_URL", "http://127.0.0.1:8080").rstrip("/")


@pytest.fixture(scope="session", autouse=True)
def ensure_backend_available(base_url: str):
    req = Request(f"{base_url}/api/meta", method="GET")
    start = time.time()
    last_error: Exception | None = None

    while time.time() - start < 8.0:
        try:
            with urlopen(req, timeout=1.5) as resp:
                if resp.status == 200:
                    return
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(0.25)

    raise RuntimeError(
        "Backend недоступен. Запусти сервер (`python -m app.server`) "
        f"или укажи TEST_BASE_URL. Последняя ошибка: {last_error}"
    )


@pytest.fixture()
def api_request(base_url: str):
    """Выполнить HTTP-запрос к живому backend и вернуть статус/тело/заголовки."""

    def _request(
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        json_payload: dict[str, Any] | None = None,
        raw_body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        query = query or {}
        params = urlencode({k: v for k, v in query.items() if v is not None})
        url = f"{base_url}{path}"
        if params:
            url = f"{url}?{params}"

        request_headers = dict(headers or {})
        body_bytes: bytes | None = raw_body
        if json_payload is not None:
            body_bytes = json.dumps(json_payload, ensure_ascii=False).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")

        request = Request(url=url, data=body_bytes, headers=request_headers, method=method.upper())

        try:
            with urlopen(request, timeout=5.0) as resp:
                raw = resp.read()
                body, text = _decode_body(raw)
                return HttpResponse(
                    status=resp.status,
                    body=body,
                    text=text,
                    headers={k.lower(): v for k, v in resp.headers.items()},
                )
        except HTTPError as err:
            raw = err.read() if err.fp else b""
            body, text = _decode_body(raw)
            return HttpResponse(
                status=err.code,
                body=body,
                text=text,
                headers={k.lower(): v for k, v in err.headers.items()} if err.headers else {},
            )
        except URLError as err:
            raise RuntimeError(f"Ошибка HTTP-запроса к backend: {err}") from err

    return _request


@pytest.fixture()
def product_payload_template() -> dict[str, Any]:
    return {
        "photos": [],
        "proteins": 0.0,
        "fats": 0.0,
        "carbs": 0.0,
        "composition": None,
        "category": "Овощи",
        "cooking_requirement": "Готовый к употреблению",
        "flags": {"vegan": True, "gluten_free": True, "sugar_free": True},
    }


@pytest.fixture()
def name_prefix() -> str:
    return f"autotest_{uuid.uuid4().hex[:8]}"


@pytest.fixture()
def cleanup_registry(api_request):
    """Регистр созданных сущностей, чтобы аккуратно удалять их после теста."""

    registry = {"dish_ids": [], "product_ids": []}
    yield registry

    for dish_id in sorted(set(registry["dish_ids"]), reverse=True):
        api_request("DELETE", f"/api/dishes/{dish_id}")
    for product_id in sorted(set(registry["product_ids"]), reverse=True):
        api_request("DELETE", f"/api/products/{product_id}")


@pytest.fixture()
def create_product_api(api_request, product_payload_template, cleanup_registry, name_prefix):
    def _create(*, name: str, calories: float, template: dict[str, Any] | None = None, **kwargs):
        payload = dict(template or product_payload_template)
        payload.update({"name": f"{name_prefix}_{name}", "calories": calories})
        payload.update(kwargs)
        response = api_request("POST", "/api/products", json_payload=payload)
        if response.status != 201:
            raise RuntimeError(f"Не удалось создать продукт: {response.status} {response.body}")
        cleanup_registry["product_ids"].append(int(response.body["id"]))
        return response.body

    return _create


@pytest.fixture()
def create_dish_api(api_request, cleanup_registry, name_prefix):
    def _create(
        *,
        name: str,
        ingredients: list[dict[str, Any]],
        category: str = "Суп",
        flags: dict[str, bool] | None = None,
        expected_status: int = 201,
    ):
        payload = {
            "name": f"{name_prefix}_{name}",
            "portion_size": 300.0,
            "category": category,
            "ingredients": ingredients,
            "photos": [],
        }
        if flags is not None:
            payload["flags"] = flags

        response = api_request("POST", "/api/dishes", json_payload=payload)
        if response.status != expected_status:
            raise RuntimeError(
                f"Неожиданный статус create dish: {response.status}, expected={expected_status}, body={response.body}"
            )
        if response.status == 201:
            cleanup_registry["dish_ids"].append(int(response.body["id"]))
            return response.body
        return response

    return _create
