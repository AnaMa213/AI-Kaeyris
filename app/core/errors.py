"""RFC 9457 Problem Details error handling.

Spec: https://www.rfc-editor.org/rfc/rfc9457.html
"""

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

PROBLEM_CONTENT_TYPE = "application/problem+json"
DEFAULT_TYPE_BASE = "https://kaeyris.local/errors"


class AppError(Exception):
    """Base class for application errors mapped to RFC 9457 Problem Details.

    Subclass and override `status_code`, `error_type`, `title` to declare a new
    error category. `detail` (instance-specific) is passed at raise time.

    `default_headers` is a tuple of (name, value) pairs added to the HTTP
    response. Used e.g. for `WWW-Authenticate` on 401. Tuple, not dict, to
    avoid the mutable-default-class-attribute pitfall.
    """

    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    error_type: str = "internal"
    title: str = "Internal Server Error"
    default_headers: tuple[tuple[str, str], ...] = ()

    def __init__(
        self,
        detail: str | None = None,
        *,
        headers: dict[str, str] | None = None,
        **extras: Any,
    ) -> None:
        super().__init__(detail or self.title)
        self.detail = detail or self.title
        self.headers: dict[str, str] = dict(self.default_headers)
        if headers:
            self.headers.update(headers)
        self.extras = extras


def _problem_response(
    *,
    status_code: int,
    error_type: str,
    title: str,
    detail: str,
    instance: str,
    extras: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"{DEFAULT_TYPE_BASE}/{error_type}",
        "title": title,
        "status": status_code,
        "detail": detail,
        "instance": instance,
    }
    if extras:
        body.update(extras)
    return JSONResponse(
        status_code=status_code,
        content=body,
        media_type=PROBLEM_CONTENT_TYPE,
        headers=headers or None,
    )


async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    return _problem_response(
        status_code=exc.status_code,
        error_type=exc.error_type,
        title=exc.title,
        detail=exc.detail,
        instance=request.url.path,
        extras=exc.extras or None,
        headers=exc.headers or None,
    )


async def _handle_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    errors = [
        {
            "location": [str(p) for p in err["loc"]],
            "issue": err["msg"],
            "type": err["type"],
        }
        for err in exc.errors()
    ]
    return _problem_response(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        error_type="validation",
        title="Validation error",
        detail="Request payload failed validation.",
        instance=request.url.path,
        extras={"errors": errors},
    )


async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    # Log server-side; never leak the stack trace to the client.
    logger.error(
        "Unhandled exception",
        exc_info=exc,
        extra={"path": request.url.path},
    )
    return _problem_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        error_type="internal",
        title="Internal Server Error",
        detail="An unexpected error occurred.",
        instance=request.url.path,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Wire RFC 9457 handlers on a FastAPI app.

    Call this once per app instance (main app or per-test app).
    """
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)
