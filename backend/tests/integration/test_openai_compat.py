"""Integration tests for OpenAI-compatible endpoints and official SDK compatibility."""

import asyncio

import httpx
import openai
import pytest
from fastapi.testclient import TestClient

from relay.auth import generate_api_key
from relay.db import create_api_key, init_db


@pytest.fixture
def auth_key(temp_db_path: str) -> tuple[str, str]:
    """Creates a valid test API key in SQLite and returns (raw_key, key_id)."""
    raw_key, key_hash, prefix = generate_api_key()
    asyncio.run(init_db(temp_db_path))
    rec = asyncio.run(
        create_api_key(
            db_path=temp_db_path,
            name="Test Suite Key",
            key_hash=key_hash,
            key_prefix=prefix,
            allowed_aliases=["*"],
        )
    )
    return raw_key, rec.id


def test_unauthorized_request(app_with_db: TestClient) -> None:
    """Verifies that requests without API key receive 401 with OpenAI error shape."""
    resp = app_with_db.get("/v1/models")
    assert resp.status_code == 401
    err = resp.json()["error"]
    assert err["code"] == "invalid_api_key"


def test_list_models_authenticated(app_with_db: TestClient, auth_key: tuple[str, str]) -> None:
    """Verifies that authenticated GET /v1/models returns model aliases."""
    raw_key, _ = auth_key
    resp = app_with_db.get("/v1/models", headers={"Authorization": f"Bearer {raw_key}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "list"
    ids = [m["id"] for m in data["data"]]
    assert "fast" in ids
    assert "smart" in ids


def test_chat_completion_non_streaming(app_with_db: TestClient, auth_key: tuple[str, str]) -> None:
    """Verifies non-streaming POST /v1/chat/completions."""
    raw_key, _ = auth_key
    payload = {
        "model": "fast",
        "messages": [{"role": "user", "content": "Hello gateway"}],
        "temperature": 0.7,
    }
    resp = app_with_db.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {raw_key}"},
        json=payload,
    )
    assert resp.status_code == 200
    assert "X-Relay-Request-Id" in resp.headers
    assert "X-Relay-Provider" in resp.headers
    assert resp.headers["X-Relay-Cache"] == "MISS"
    assert resp.headers["X-Relay-Attempts"] == "1"

    data = resp.json()
    assert data["object"] == "chat.completion"
    assert len(data["choices"]) == 1
    assert data["choices"][0]["message"]["role"] == "assistant"
    assert data["usage"]["total_tokens"] > 0


def test_chat_completion_streaming(app_with_db: TestClient, auth_key: tuple[str, str]) -> None:
    """Verifies streaming POST /v1/chat/completions over SSE."""
    raw_key, _ = auth_key
    payload = {
        "model": "fast",
        "messages": [{"role": "user", "content": "Stream me a response"}],
        "stream": True,
    }
    with app_with_db.stream(
        "POST",
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {raw_key}"},
        json=payload,
    ) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        lines = [line.strip() for line in response.iter_lines() if line.strip()]
        assert len(lines) >= 3
        # Should start with data: and end with data: [DONE]
        assert lines[0].startswith("data: ")
        assert lines[-1] == "data: [DONE]"


@pytest.mark.asyncio
async def test_official_openai_sdk_compatibility(
    app_with_db: TestClient, auth_key: tuple[str, str]
) -> None:
    """Verifies that the official OpenAI Python SDK works seamlessly against Relay."""
    raw_key, _ = auth_key

    # Configure async client using FastAPI ASGI transport
    async_http_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_with_db.app),
        base_url="http://testserver",
    )

    client = openai.AsyncOpenAI(
        api_key=raw_key,
        base_url="http://testserver/v1",
        http_client=async_http_client,
    )

    # 1. Non-streaming call via SDK
    completion = await client.chat.completions.create(
        model="smart",
        messages=[{"role": "user", "content": "Test with official SDK"}],
    )
    assert completion.choices[0].message.content is not None
    assert completion.model == "smart"

    # 2. Streaming call via SDK
    stream = await client.chat.completions.create(
        model="fast",
        messages=[{"role": "user", "content": "Test stream via SDK"}],
        stream=True,
    )
    collected_tokens = []
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            collected_tokens.append(chunk.choices[0].delta.content)

    assert len(collected_tokens) > 0
    await async_http_client.aclose()
