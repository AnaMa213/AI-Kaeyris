from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


async def test_echo_returns_payload(template_app: FastAPI):
    transport = ASGITransport(app=template_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/services/_template/echo", json={"message": "hello"}
        )

    assert response.status_code == 200
    assert response.json() == {"echo": "hello"}


async def test_echo_rejects_missing_message(template_app: FastAPI):
    transport = ASGITransport(app=template_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/services/_template/echo", json={})

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["type"] == "https://kaeyris.local/errors/validation"
    assert body["status"] == 422
    assert body["instance"] == "/services/_template/echo"
    assert any(err["location"] == ["body", "message"] for err in body["errors"])


async def test_echo_rejects_empty_message(template_app: FastAPI):
    transport = ASGITransport(app=template_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/services/_template/echo", json={"message": ""}
        )

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
