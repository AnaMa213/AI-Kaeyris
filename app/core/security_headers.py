"""Security headers middleware (OWASP Secure Headers Project).

ADR 0003. Reference: https://owasp.org/www-project-secure-headers/

Note on documentation routes: FastAPI's built-in /docs and /redoc are HTML
pages that load Swagger UI / ReDoc bundles from a public CDN. The strict
``Content-Security-Policy: default-src 'none'`` blocks every external asset
(including the favicon) and renders the page blank. We drop the CSP header
on the FastAPI documentation paths only — every other security header
(X-Content-Type-Options, X-Frame-Options, Referrer-Policy, HSTS) stays. The
documentation paths are already public by design (see ADR 0003 §5).
"""

from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# `setdefault` semantics: a route that explicitly sets one of these (e.g. a
# stricter CSP for a specific endpoint) overrides the default below.
SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'",
    # HSTS only takes effect over HTTPS. Harmless over plain HTTP today,
    # ready for when Caddy fronts the API at Jalon 8.
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}

# Paths that serve HTML documentation with external CDN assets. CSP is
# skipped on these paths only; every other security header still applies.
DOC_EXEMPT_PATHS: tuple[str, ...] = ("/docs", "/redoc", "/openapi.json")


def _is_doc_path(path: str) -> bool:
    return any(path == p or path.startswith(p + "/") for p in DOC_EXEMPT_PATHS)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add OWASP-recommended response headers to every API response."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        skip_csp = _is_doc_path(request.url.path)
        for header, value in SECURITY_HEADERS.items():
            if skip_csp and header == "Content-Security-Policy":
                continue
            response.headers.setdefault(header, value)
        return response
