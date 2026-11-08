"""Unit tests for health check endpoints."""

from fastapi.testclient import TestClient


def test_healthz_endpoint(app_with_db: TestClient) -> None:
    """Verifies that /healthz returns 200 OK and expected JSON body."""
    response = app_with_db.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "relay"
    assert "X-Relay-Request-Id" in response.headers
    assert "X-Relay-Latency-Ms" in response.headers


def test_readyz_endpoint(app_with_db: TestClient) -> None:
    """Verifies that /readyz checks SQLite and Redis."""
    response = app_with_db.get("/readyz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "components" in data
    assert data["components"]["database"] == "ok"
