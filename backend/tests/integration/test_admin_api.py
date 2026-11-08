"""Integration tests for all /admin endpoints including chaos controls, keys, and SSE events."""

import json

from fastapi.testclient import TestClient


def test_admin_endpoints_require_auth(app_with_db: TestClient) -> None:
    """Verifies that unauthenticated calls to /admin are rejected with 401."""
    resp = app_with_db.get("/admin/providers")
    assert resp.status_code == 401

    resp_invalid = app_with_db.get(
        "/admin/providers", headers={"Authorization": "Bearer wrong_token"}
    )
    assert resp_invalid.status_code == 403


def test_admin_list_and_control_providers(app_with_db: TestClient) -> None:
    """Verifies listing providers, updating chaos knobs, and resetting chaos."""
    headers = {"Authorization": "Bearer test_admin_key"}

    # 1. List providers
    resp = app_with_db.get("/admin/providers", headers=headers)
    assert resp.status_code == 200
    providers = resp.json()
    assert len(providers) >= 3
    names = [p["name"] for p in providers]
    assert "mock-a" in names
    assert "mock-b" in names

    # 2. Update chaos on mock-a (kill provider)
    chaos_payload = {
        "provider": "mock-a",
        "is_down": True,
        "base_latency_ms": 120.0,
        "error_rate": 0.25,
    }
    chaos_resp = app_with_db.post("/admin/chaos", headers=headers, json=chaos_payload)
    assert chaos_resp.status_code == 200
    data = chaos_resp.json()
    assert data["provider"] == "mock-a"
    assert data["chaos"]["is_down"] is True
    assert data["chaos"]["base_latency_ms"] == 120.0

    # 3. Reset all chaos
    reset_resp = app_with_db.post("/admin/chaos/reset", headers=headers)
    assert reset_resp.status_code == 200


def test_admin_key_lifecycle(app_with_db: TestClient) -> None:
    """Verifies creating, listing, updating, and rotating API keys."""
    headers = {"Authorization": "Bearer test_admin_key"}

    # 1. Create Key
    create_payload = {
        "name": "Integration Test Key",
        "rpm_limit": 100,
        "tpm_limit": 80000,
        "monthly_budget_usd": 75.0,
        "allowed_aliases": ["fast"],
    }
    create_resp = app_with_db.post("/admin/keys", headers=headers, json=create_payload)
    assert create_resp.status_code == 200
    key_data = create_resp.json()
    key_id = key_data["id"]
    secret = key_data["secret"]
    assert secret.startswith("rl_live_")
    assert key_data["name"] == "Integration Test Key"

    # 2. List keys
    list_resp = app_with_db.get("/admin/keys", headers=headers)
    assert list_resp.status_code == 200
    keys = list_resp.json()
    assert any(k["id"] == key_id for k in keys)

    # 3. Update key
    update_payload = {"rpm_limit": 150, "enabled": False}
    update_resp = app_with_db.patch(f"/admin/keys/{key_id}", headers=headers, json=update_payload)
    assert update_resp.status_code == 200
    assert update_resp.json()["rpm_limit"] == 150
    assert update_resp.json()["enabled"] is False

    # 4. Rotate key
    rotate_resp = app_with_db.post(f"/admin/keys/{key_id}/rotate", headers=headers)
    assert rotate_resp.status_code == 200
    new_secret = rotate_resp.json()["secret"]
    assert new_secret != secret
    assert new_secret.startswith("rl_live_")


def test_admin_config_and_stats(app_with_db: TestClient) -> None:
    """Verifies viewing router config, reloading config, and fetching dashboard stats."""
    headers = {"Authorization": "Bearer test_admin_key"}

    # 1. Config view
    cfg_resp = app_with_db.get("/admin/config", headers=headers)
    assert cfg_resp.status_code == 200
    assert "fast" in cfg_resp.json()["aliases"]

    # 2. Config reload
    reload_resp = app_with_db.post("/admin/config/reload", headers=headers)
    assert reload_resp.status_code == 200
    assert reload_resp.json()["status"] == "ok"

    # 3. Stats summary
    stats_resp = app_with_db.get("/admin/stats", headers=headers)
    assert stats_resp.status_code == 200
    stats = stats_resp.json()
    assert "summary" in stats
    assert "total_requests" in stats["summary"]
    assert "recent_logs" in stats


def test_admin_events_sse_stream(app_with_db: TestClient) -> None:
    """Verifies that GET /admin/events connects to SSE stream and receives initial ping."""
    headers = {"Authorization": "Bearer test_admin_key"}

    with app_with_db.stream("GET", "/admin/events?limit=1", headers=headers) as stream:
        assert stream.status_code == 200
        assert "text/event-stream" in stream.headers["content-type"]

        lines = [line.strip() for line in stream.iter_lines() if line.strip()]
        assert len(lines) >= 2
        assert lines[0] == "event: ping"
        assert lines[1].startswith("data: ")
        data = json.loads(lines[1][6:])
        assert "time" in data
