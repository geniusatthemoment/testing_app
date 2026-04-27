from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import quote

from aiohttp import web

from .constants import COOKING_REQUIREMENTS, DISH_CATEGORIES, FLAG_KEYS, PRODUCT_CATEGORIES
from .errors import ConflictError, NotFoundError, ValidationError
from .service import RecipeBookService


def _parse_bool_param(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y"}:
        return True
    if normalized in {"0", "false", "no", "n"}:
        return False
    raise ValidationError(f"Некорректное булево значение: '{value}'")


@web.middleware
async def error_middleware(request: web.Request, handler):
    try:
        return await handler(request)
    except ValidationError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except NotFoundError as exc:
        return web.json_response({"error": str(exc)}, status=404)
    except ConflictError as exc:
        text = str(exc)
        try:
            payload = json.loads(text)
            return web.json_response(payload, status=409)
        except json.JSONDecodeError:
            return web.json_response({"error": text}, status=409)


async def index_handler(request: web.Request) -> web.Response:
    static_dir = Path(__file__).resolve().parent.parent / "static"
    return web.FileResponse(static_dir / "index.html")


async def meta_handler(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "product_categories": sorted(PRODUCT_CATEGORIES),
            "cooking_requirements": sorted(COOKING_REQUIREMENTS),
            "dish_categories": sorted(DISH_CATEGORIES),
            "flags": list(FLAG_KEYS),
        }
    )


def _resolve_upload_ext(filename: str | None, content_type: str | None) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}:
        return suffix

    if content_type == "image/jpeg":
        return ".jpg"
    if content_type == "image/png":
        return ".png"
    if content_type == "image/webp":
        return ".webp"
    if content_type == "image/gif":
        return ".gif"
    if content_type == "image/bmp":
        return ".bmp"

    raise ValidationError("Поддерживаются только изображения: jpg, png, webp, gif, bmp")


async def upload_photo(request: web.Request) -> web.Response:
    uploads_dir: Path = request.app["uploads_dir"]
    try:
        reader = await request.multipart()
    except (AssertionError, ValueError) as exc:
        raise ValidationError("Ожидается multipart/form-data запрос") from exc
    field = await reader.next()

    if field is None or field.name != "file":
        raise ValidationError("Ожидается multipart с полем 'file'")
    if not field.filename:
        raise ValidationError("Не передано имя файла")

    content_type = (field.headers.get("Content-Type") or "").lower().split(";", 1)[0].strip()
    if content_type and not content_type.startswith("image/"):
        raise ValidationError("Можно загружать только изображения")

    ext = _resolve_upload_ext(field.filename, content_type)
    generated_name = f"{secrets.token_hex(16)}{ext}"
    file_path = uploads_dir / generated_name

    total_size = 0
    max_size = 10 * 1024 * 1024
    with file_path.open("wb") as out:
        while True:
            chunk = await field.read_chunk(64 * 1024)
            if not chunk:
                break
            total_size += len(chunk)
            if total_size > max_size:
                file_path.unlink(missing_ok=True)
                raise ValidationError("Файл слишком большой: максимум 10 МБ")
            out.write(chunk)

    return web.json_response(
        {
            "url": f"/uploads/{quote(generated_name)}",
            "filename": generated_name,
            "size": total_size,
        },
        status=201,
    )


async def create_product(request: web.Request) -> web.Response:
    service: RecipeBookService = request.app["service"]
    payload = await request.json()
    result = service.create_product(payload)
    return web.json_response(result, status=201)


async def list_products(request: web.Request) -> web.Response:
    service: RecipeBookService = request.app["service"]
    filters: dict[str, Any] = {
        "category": request.query.get("category"),
        "cooking_requirement": request.query.get("cooking_requirement"),
        "q": request.query.get("q"),
        "sort_by": request.query.get("sort_by", "name"),
        "order": request.query.get("order", "asc"),
    }
    for flag in FLAG_KEYS:
        filters[flag] = _parse_bool_param(request.query.get(flag))

    result = service.list_products(filters)
    return web.json_response(result)


async def get_product(request: web.Request) -> web.Response:
    service: RecipeBookService = request.app["service"]
    product_id = int(request.match_info["product_id"])
    return web.json_response(service.get_product(product_id))


async def update_product(request: web.Request) -> web.Response:
    service: RecipeBookService = request.app["service"]
    product_id = int(request.match_info["product_id"])
    payload = await request.json()
    return web.json_response(service.update_product(product_id, payload))


async def delete_product(request: web.Request) -> web.Response:
    service: RecipeBookService = request.app["service"]
    product_id = int(request.match_info["product_id"])
    service.delete_product(product_id)
    return web.Response(status=204)


async def create_dish(request: web.Request) -> web.Response:
    service: RecipeBookService = request.app["service"]
    payload = await request.json()
    result = service.create_dish(payload)
    return web.json_response(result, status=201)


async def list_dishes(request: web.Request) -> web.Response:
    service: RecipeBookService = request.app["service"]
    filters: dict[str, Any] = {
        "category": request.query.get("category"),
        "q": request.query.get("q"),
    }
    for flag in FLAG_KEYS:
        filters[flag] = _parse_bool_param(request.query.get(flag))

    result = service.list_dishes(filters)
    return web.json_response(result)


async def get_dish(request: web.Request) -> web.Response:
    service: RecipeBookService = request.app["service"]
    dish_id = int(request.match_info["dish_id"])
    return web.json_response(service.get_dish(dish_id))


async def update_dish(request: web.Request) -> web.Response:
    service: RecipeBookService = request.app["service"]
    dish_id = int(request.match_info["dish_id"])
    payload = await request.json()
    return web.json_response(service.update_dish(dish_id, payload))


async def delete_dish(request: web.Request) -> web.Response:
    service: RecipeBookService = request.app["service"]
    dish_id = int(request.match_info["dish_id"])
    service.delete_dish(dish_id)
    return web.Response(status=204)


def create_app(db_path: str = "data/recipe_book.db", uploads_path: str = "data/uploads") -> web.Application:
    app = web.Application(middlewares=[error_middleware])
    app["service"] = RecipeBookService(db_path)

    static_dir = Path(__file__).resolve().parent.parent / "static"
    uploads_dir = Path(uploads_path)
    uploads_dir.mkdir(parents=True, exist_ok=True)
    app["uploads_dir"] = uploads_dir

    app.router.add_get("/", index_handler)
    app.router.add_static("/static/", str(static_dir), show_index=True)
    app.router.add_static("/uploads/", str(uploads_dir), show_index=False)

    app.router.add_get("/api/meta", meta_handler)
    app.router.add_post("/api/uploads", upload_photo)

    app.router.add_post("/api/products", create_product)
    app.router.add_get("/api/products", list_products)
    app.router.add_get(r"/api/products/{product_id:\d+}", get_product)
    app.router.add_put(r"/api/products/{product_id:\d+}", update_product)
    app.router.add_delete(r"/api/products/{product_id:\d+}", delete_product)

    app.router.add_post("/api/dishes", create_dish)
    app.router.add_get("/api/dishes", list_dishes)
    app.router.add_get(r"/api/dishes/{dish_id:\d+}", get_dish)
    app.router.add_put(r"/api/dishes/{dish_id:\d+}", update_dish)
    app.router.add_delete(r"/api/dishes/{dish_id:\d+}", delete_dish)

    return app
