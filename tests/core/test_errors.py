from fastapi import FastAPI, status
from httpx import ASGITransport, AsyncClient

from app.core.errors import AppError, register_exception_handlers


class _TeapotError(AppError):
    status_code = status.HTTP_418_IM_A_TEAPOT
    error_type = "teapot"
    title = "I'm a teapot"


def _make_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/raise-app-error")
    def _raise_app_error() -> None:
        raise _TeapotError(detail="No coffee here")

    @app.get("/raise-unexpected")
    def _raise_unexpected() -> None:
        raise RuntimeError("boom")

    return app


async def test_app_error_renders_problem_details():
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/raise-app-error")

    assert response.status_code == 418
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "https://kaeyris.local/errors/teapot",
        "title": "I'm a teapot",
        "status": 418,
        "detail": "No coffee here",
        "instance": "/raise-app-error",
    }


async def test_unexpected_exception_returns_generic_500():
    app = _make_app()
    # raise_app_exceptions=False lets the registered handler catch RuntimeError
    # rather than re-raising it through the test client.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/raise-unexpected")

    assert response.status_code == 500
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["status"] == 500
    assert body["type"] == "https://kaeyris.local/errors/internal"
    assert body["instance"] == "/raise-unexpected"
    assert "boom" not in body["detail"]
