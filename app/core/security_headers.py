"""Security headers middleware (OWASP Secure Headers Project).

ADR 0003. Reference: https://owasp.org/www-project-secure-headers/
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


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add OWASP-recommended response headers to every API response."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response
