"""Integration tests for monthly budget limits and HTTP 402 rejection."""

import asyncio
from datetime import UTC, datetime

import aiosqlite
from fastapi.testclient import TestClient

from relay.auth import generate_api_key
from relay.db import create_api_key


def test_budget_exceeded_returns_402(app_with_db: TestClient, temp_db_path: str) -> None:
    """Verifies that requests are rejected with 402 when key monthly budget is exhausted."""
    raw_key, key_hash, prefix = generate_api_key()
    rec = asyncio.run(
        create_api_key(
            db_path=temp_db_path,
            name="Low Budget Client",
            key_hash=key_hash,
            key_prefix=prefix,
            monthly_budget_usd=0.0050,  # $0.005 budget
            allowed_aliases=["*"],
        )
    )

    # Initially under budget -> request succeeds
    resp1 = app_with_db.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={
            "model": "fast",
            "messages": [{"role": "user", "content": "First prompt"}],
        },
    )
    assert resp1.status_code == 200

    # Directly insert request log row in SQLite that exhausts the budget
    insert_sql = """
    INSERT INTO request_logs (
        id, timestamp, api_key_id, alias, provider, upstream_model,
        status, attempts, cache_status, latency_ms, ttft_ms,
        tokens_in, tokens_out, cost_usd, error_type
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    async def insert_spend() -> None:
        async with aiosqlite.connect(temp_db_path) as db:
            await db.execute(
                insert_sql,
                (
                    "spend-drain-1",
                    datetime.now(UTC).isoformat(),
                    rec.id,
                    "smart",
                    "mock-a",
                    "mock-gpt-4o",
                    200,
                    1,
                    "MISS",
                    100.0,
                    20.0,
                    1000,
                    2000,
                    0.0100,  # Exceeds the $0.005 budget
                    None,
                ),
            )
            await db.commit()

    asyncio.run(insert_spend())

    # Second request should be rejected with HTTP 402 Payment Required!
    resp2 = app_with_db.post(
        "/v1/chat/completions",
        headers={"Authorization": f"Bearer {raw_key}"},
        json={
            "model": "fast",
            "messages": [{"role": "user", "content": "Second prompt"}],
        },
    )
    assert resp2.status_code == 402
    err_data = resp2.json()["error"]
    assert err_data["code"] == 402
    assert "budget" in err_data["message"].lower()
