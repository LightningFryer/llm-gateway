"""Integration tests verifying transparent failover, circuit breaker tripping, and streaming failure semantics."""

import json

import pytest
from fastapi.testclient import TestClient

from relay.circuit_breaker import CircuitState, get_circuit_breaker
from relay.providers import MockProvider, get_provider


def test_non_streaming_transparent_failover(
    app_with_db: TestClient, auth_key: tuple[str, str]
) -> None:
    """Verifies that when primary provider is forced DOWN, request transparently fails over to secondary."""
    raw_key, _ = auth_key
    mock_a = get_provider("mock-a")
    mock_b = get_provider("mock-b")
    assert isinstance(mock_a, MockProvider)
    assert isinstance(mock_b, MockProvider)

    # Force primary DOWN
    mock_a.update_chaos(is_down=True)
    mock_b.reset_chaos()

    try:
        resp = app_with_db.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {raw_key}"},
            json={
                "model": "smart",
                "messages": [{"role": "user", "content": "Test failover"}],
            },
        )
        assert resp.status_code == 200
        # Headers should indicate failover occurred
        assert resp.headers["X-Relay-Provider"] == "mock-b"
        assert resp.headers["X-Relay-Attempts"] == "2"
        data = resp.json()
        assert "[mock-b]" in data["choices"][0]["message"]["content"]
    finally:
        mock_a.reset_chaos()


def test_streaming_transparent_pre_token_failover(
    app_with_db: TestClient, auth_key: tuple[str, str]
) -> None:
    """Verifies that pre-first-token streaming failure transparently falls over to secondary provider."""
    raw_key, _ = auth_key
    mock_a = get_provider("mock-a")
    mock_b = get_provider("mock-b")
    assert isinstance(mock_a, MockProvider)
    assert isinstance(mock_b, MockProvider)

    # Force primary DOWN before stream starts
    mock_a.update_chaos(is_down=True)
    mock_b.reset_chaos()

    try:
        with app_with_db.stream(
            "POST",
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {raw_key}"},
            json={
                "model": "smart",
                "messages": [{"role": "user", "content": "Test streaming pre-token failover"}],
                "stream": True,
            },
        ) as response:
            assert response.status_code == 200
            lines = [line.strip() for line in response.iter_lines() if line.strip()]
            assert len(lines) >= 3
            assert lines[-1] == "data: [DONE]"

            # Verify content came from mock-b
            content_chunks = []
            for line in lines:
                if line.startswith("data: ") and line != "data: [DONE]":
                    chunk_json = json.loads(line[6:])
                    delta = chunk_json["choices"][0]["delta"].get("content")
                    if delta:
                        content_chunks.append(delta)
            full_text = "".join(content_chunks)
            assert "[mock-b]" in full_text
    finally:
        mock_a.reset_chaos()


def test_streaming_mid_stream_failure_terminal_event(
    app_with_db: TestClient, auth_key: tuple[str, str]
) -> None:
    """Verifies that mid-stream failure sends a terminal error SSE event and terminates cleanly."""
    raw_key, _ = auth_key
    mock_a = get_provider("mock-a")
    assert isinstance(mock_a, MockProvider)

    # Configure mock-a to fail mid-stream after 2 tokens
    mock_a.reset_chaos()
    mock_a.update_chaos(fail_after_tokens=2)

    try:
        with app_with_db.stream(
            "POST",
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {raw_key}"},
            json={
                "model": "fast",
                "messages": [
                    {
                        "role": "user",
                        "content": "This is a long test prompt that will fail mid-stream",
                    }
                ],
                "stream": True,
            },
        ) as response:
            assert response.status_code == 200
            lines = [line.strip() for line in response.iter_lines() if line.strip()]

            # Must contain an event: error line and terminate with data: [DONE]
            has_error_event = any(line.startswith("event: error") for line in lines)
            assert has_error_event is True
            assert lines[-1] == "data: [DONE]"

            # Inspect error payload
            error_line = None
            for i, line in enumerate(lines):
                if line.startswith("event: error") and i + 1 < len(lines):
                    error_line = lines[i + 1]
                    break

            assert error_line is not None
            assert error_line.startswith("data: ")
            err_data = json.loads(error_line[6:])
            assert "error" in err_data
            assert "mid-stream" in err_data["error"]["message"].lower()
    finally:
        mock_a.reset_chaos()


@pytest.mark.asyncio
async def test_circuit_breaker_skips_open_targets(
    app_with_db: TestClient, auth_key: tuple[str, str]
) -> None:
    """Verifies that when a provider's circuit breaker is forced OPEN, the gateway immediately skips it."""
    raw_key, _ = auth_key
    cb_a = get_circuit_breaker("mock-a")
    await cb_a.force_state(CircuitState.OPEN)

    try:
        resp = app_with_db.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {raw_key}"},
            json={
                "model": "smart",
                "messages": [{"role": "user", "content": "Route around open circuit"}],
            },
        )
        assert resp.status_code == 200
        # Directly routed to mock-b on attempt 1 because mock-a was skipped upfront!
        assert resp.headers["X-Relay-Provider"] == "mock-b"
        assert resp.headers["X-Relay-Attempts"] == "1"
    finally:
        await cb_a.force_state(CircuitState.CLOSED)
