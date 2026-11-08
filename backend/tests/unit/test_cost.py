"""Unit tests for token estimation, pricing calculation, and budget checks."""

from datetime import UTC, datetime

import aiosqlite
import pytest

from relay.cost import check_budget_available, estimate_messages_tokens, estimate_tokens
from relay.db import create_api_key, init_db


def test_estimate_tokens() -> None:
    """Verifies that token estimation returns realistic token counts."""
    assert estimate_tokens("") == 0
    short_text = "Hello world from Relay LLM gateway"
    tokens = estimate_tokens(short_text)
    assert 4 <= tokens <= 12

    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Explain circuit breakers in distributed systems."},
    ]
    msg_tokens = estimate_messages_tokens(messages)
    assert msg_tokens > 15


@pytest.mark.asyncio
async def test_budget_enforcement_check(temp_db_path: str) -> None:
    """Verifies check_budget_available triggers when spend exceeds monthly budget."""
    await init_db(temp_db_path)

    key = await create_api_key(
        db_path=temp_db_path,
        name="Budget Key",
        key_hash="hash_budget",
        key_prefix="rl_live_bdgt",
        monthly_budget_usd=0.0100,  # $0.01 budget
    )

    # Initially allowed (spend is $0.0)
    allowed, spend = await check_budget_available(temp_db_path, key.id, key.monthly_budget_usd)
    assert allowed is True
    assert spend == 0.0

    # Insert log directly into SQLite simulating $0.0150 spend
    insert_sql = """
    INSERT INTO request_logs (
        id, timestamp, api_key_id, alias, provider, upstream_model,
        status, attempts, cache_status, latency_ms, ttft_ms,
        tokens_in, tokens_out, cost_usd, error_type
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    async with aiosqlite.connect(temp_db_path) as db:
        await db.execute(
            insert_sql,
            (
                "log-budget-1",
                datetime.now(UTC).isoformat(),
                key.id,
                "smart",
                "mock-a",
                "mock-gpt-4o",
                200,
                1,
                "MISS",
                50.0,
                15.0,
                500,
                1000,
                0.0150,
                None,
            ),
        )
        await db.commit()

    # Now budget check should fail!
    allowed_now, current_spend = await check_budget_available(
        temp_db_path, key.id, key.monthly_budget_usd
    )
    assert allowed_now is False
    assert current_spend >= 0.0100
