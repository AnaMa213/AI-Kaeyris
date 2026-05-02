from fastapi import FastAPI

from app.core.config import settings
from app.core.errors import register_exception_handlers

app = FastAPI(
    title="AI-Kaeyris",
    version=settings.APP_VERSION,
    description="Plateforme AI personnelle — monolithe modulaire FastAPI.",
)
register_exception_handlers(app)


@app.get("/health", tags=["health"], summary="Vérifie que l'API est en vie.")
def health() -> dict[str, str]:
    return {"status": "ok", "version": settings.APP_VERSION}
