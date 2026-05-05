from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.core.security_headers import (
    DOC_EXEMPT_PATHS,
    SECURITY_HEADERS,
    SecurityHeadersMiddleware,
)


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


# ----- Documentation endpoints exemption (jalon 5 hotfix) -----
#
# Swagger UI and ReDoc load assets (JS, CSS, favicon) from external CDNs by
# default; the strict CSP `default-src 'none'` blocks every one of them and
# renders /docs as a blank page. The middleware exempts the documentation
# paths from CSP only — every other security header is still applied.


async def test_docs_does_not_get_strict_csp():
    app = FastAPI()  # FastAPI registers /docs, /redoc, /openapi.json by default
    app.add_middleware(SecurityHeadersMiddleware)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/docs")

    assert response.status_code == 200
    # CSP must NOT be applied so Swagger UI can load its assets.
    assert "content-security-policy" not in {h.lower() for h in response.headers}


async def test_docs_keeps_other_security_headers():
    """Only CSP is dropped — X-Frame-Options, nosniff, etc. stay."""
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/docs")

    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert response.headers.get("Referrer-Policy") == "no-referrer"


async def test_openapi_json_also_exempt_from_csp():
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/openapi.json")

    assert response.status_code == 200
    assert "content-security-policy" not in {h.lower() for h in response.headers}


def test_doc_exempt_paths_covers_known_doc_routes():
    """Spec invariant: the exempt list matches FastAPI's documentation routes."""
    assert "/docs" in DOC_EXEMPT_PATHS
    assert "/redoc" in DOC_EXEMPT_PATHS
    assert "/openapi.json" in DOC_EXEMPT_PATHS
