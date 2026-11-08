"""Integration tests verifying exact-match caching, streaming replay, and cache bypass."""

from fastapi.testclient import TestClient


def test_exact_match_cache_hit_and_miss(app_with_db: TestClient, auth_key: tuple[str, str]) -> None:
    """Verifies that deterministic prompts are cached and second request returns cache HIT."""
    raw_key, _ = auth_key
    payload = {
        "model": "smart",
        "messages": [{"role": "user", "content": "What is the capital of France?"}],
        "temperature": 0.0,
    }

    # First request -> Cache MISS
    resp1 = app_with_db.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {raw_key}"},
        json=payload,
    )
    assert resp1.status_code == 200
    assert resp1.headers["X-Relay-Cache"] == "MISS"
    text1 = resp1.json()["choices"][0]["message"]["content"]

    # Second identical request -> Cache HIT
    resp2 = app_with_db.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {raw_key}"},
        json=payload,
    )
    assert resp2.status_code == 200
    assert resp2.headers["X-Relay-Cache"] == "HIT"
    assert resp2.headers["X-Relay-Provider"] == "cache"
    assert resp2.headers["X-Relay-Attempts"] == "0"
    text2 = resp2.json()["choices"][0]["message"]["content"]
    assert text1 == text2


def test_cache_bypass_via_header(app_with_db: TestClient, auth_key: tuple[str, str]) -> None:
    """Verifies that 'Cache-Control: no-cache' header bypasses the cache."""
    raw_key, _ = auth_key
    payload = {
        "model": "fast",
        "messages": [{"role": "user", "content": "Bypass test prompt"}],
        "temperature": 0.0,
    }

    # Prime cache
    resp1 = app_with_db.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {raw_key}"},
        json=payload,
    )
    assert resp1.status_code == 200
    assert resp1.headers["X-Relay-Cache"] == "MISS"

    # Bypass cache with Cache-Control header
    resp2 = app_with_db.post(
        "/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {raw_key}",
            "Cache-Control": "no-cache",
        },
        json=payload,
    )
    assert resp2.status_code == 200
    assert resp2.headers["X-Relay-Cache"] == "MISS"


def test_streaming_cache_replay(app_with_db: TestClient, auth_key: tuple[str, str]) -> None:
    """Verifies that cached completions can be replayed over SSE for streaming clients."""
    raw_key, _ = auth_key
    payload = {
        "model": "fast",
        "messages": [{"role": "user", "content": "Stream replay prompt"}],
        "temperature": 0.0,
    }

    # Prime the cache with non-streaming request
    resp1 = app_with_db.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {raw_key}"},
        json=payload,
    )
    assert resp1.status_code == 200
    assert resp1.headers["X-Relay-Cache"] == "MISS"

    # Request identical prompt via streaming
    payload["stream"] = True
    with app_with_db.stream(
        "POST",
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {raw_key}"},
        json=payload,
    ) as stream_resp:
        assert stream_resp.status_code == 200
        assert stream_resp.headers["X-Relay-Cache"] == "HIT"
        lines = [line.strip() for line in stream_resp.iter_lines() if line.strip()]
        assert len(lines) >= 3
        assert lines[0].startswith("data: ")
        assert lines[-1] == "data: [DONE]"
