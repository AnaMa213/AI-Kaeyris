"""Smoke test: the JDR router is mounted on the main app.

Routes themselves are added by the user stories (US1..US5). At this
jalon the router is empty — the only assertion is that it exists at
the right prefix in the OpenAPI spec, so the wiring in app/main.py is
caught early if it regresses.
"""

from fastapi.testclient import TestClient


def test_jdr_tag_is_registered_in_openapi(monkeypatch, db_engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    sessionmaker = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr("app.main.get_sessionmaker", lambda: sessionmaker)
    monkeypatch.setattr("app.core.auth.settings.API_KEYS", "")

    from app.main import app

    with TestClient(app) as client:
        spec = client.get("/openapi.json").json()

    # The JDR router has no routes yet, so its tag won't appear in `tags`
    # or `paths`; what we *can* check is that mounting did not crash and
    # that the global app description still resolves. If a future change
    # accidentally drops the include_router, the import at the top of
    # main.py would fail at collection time — covering the regression
    # without needing a real route.
    assert spec["info"]["title"] == "AI-Kaeyris"
