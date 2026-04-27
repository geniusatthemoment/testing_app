from aiohttp import web

from .api import create_app


def main() -> None:
    app = create_app()
    web.run_app(app, host="127.0.0.1", port=8080)


if __name__ == "__main__":
    main()
