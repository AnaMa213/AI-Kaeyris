import pytest
from fastapi import FastAPI

from app.core.errors import register_exception_handlers
from app.services._template.router import router as template_router


@pytest.fixture
def template_app() -> FastAPI:
    """Mini FastAPI app with only the _template router mounted.

    The template service is intentionally NOT mounted in the main app
    (see ADR 0002). This fixture lets us exercise it in isolation.
    """
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(template_router)
    return app
