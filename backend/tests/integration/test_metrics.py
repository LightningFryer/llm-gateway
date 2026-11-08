"""Integration tests for /metrics Prometheus exposition."""

from fastapi.testclient import TestClient


def test_metrics_exposition(app_with_db: TestClient, auth_key: tuple[str, str]) -> None:
    """Verifies that /metrics endpoint exposes Prometheus metric counters and histograms."""
    raw_key, _ = auth_key

    # Make a request to generate metrics
    resp = app_with_db.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={
            "model": "fast",
            "messages": [{"role": "user", "content": "Metric test"}],
        },
    )
    assert resp.status_code == 200

    # Scrape /metrics
    metrics_resp = app_with_db.get("/metrics")
    assert metrics_resp.status_code == 200
    metrics_text = metrics_resp.text

    # Verify standard metrics exist
    assert "relay_requests_total" in metrics_text
    assert "relay_request_duration_seconds" in metrics_text
    assert "relay_tokens_total" in metrics_text
    assert "relay_cost_usd_total" in metrics_text
