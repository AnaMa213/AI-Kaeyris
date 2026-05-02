from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.security_headers import SECURITY_HEADERS, SecurityHeadersMiddleware


def _make_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/ping")
    def _ping() -> dict[str, str]:
        return {"pong": "yes"}

    return app


async def test_security_headers_are_present_on_success():
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/ping")

    assert response.status_code == 200
    for header, expected in SECURITY_HEADERS.items():
        assert response.headers.get(header) == expected, header


async def test_security_headers_are_present_on_404():
    app = _make_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/does-not-exist")

    assert response.status_code == 404
    for header, expected in SECURITY_HEADERS.items():
        assert response.headers.get(header) == expected, header
