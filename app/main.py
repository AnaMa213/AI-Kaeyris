from fastapi import FastAPI

from app.core.config import settings

app = FastAPI()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": settings.APP_VERSION}
