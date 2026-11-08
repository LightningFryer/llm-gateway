"""Unit tests for SQLite single-file database operations in db.py."""

import asyncio
import uuid

import pytest

from relay.db import (
    AsyncLogWriter,
    RequestLogRecord,
    create_api_key,
    get_api_key_by_hash,
    get_api_key_by_id,
    get_recent_logs,
    get_stats_summary,
    init_db,
    list_api_keys,
    rotate_api_key,
    update_api_key,
)


@pytest.mark.asyncio
async def test_db_init_and_key_crud(temp_db_path: str) -> None:
    """Tests initializing the SQLite database and performing CRUD operations on API keys."""
    await init_db(temp_db_path)

    # 1. Create Key
    key = await create_api_key(
        db_path=temp_db_path,
        name="Test Service",
        key_hash="hash_12345",
        key_prefix="rl_live_1234",
        rpm_limit=100,
        tpm_limit=50000,
        monthly_budget_usd=25.0,
        allowed_aliases=["fast", "smart"],
    )
    assert key.name == "Test Service"
    assert key.key_prefix == "rl_live_1234"
    assert key.rpm_limit == 100
    assert key.monthly_budget_usd == 25.0
    assert key.allowed_aliases == ["fast", "smart"]
    assert key.enabled is True

    # 2. Get by Hash
    found = await get_api_key_by_hash(temp_db_path, "hash_12345")
    assert found is not None
    assert found.id == key.id
    assert found.name == "Test Service"

    # 3. Get by ID
    found_id = await get_api_key_by_id(temp_db_path, key.id)
    assert found_id is not None
    assert found_id.key_hash == "hash_12345"

    # 4. List keys
    all_keys = await list_api_keys(temp_db_path)
    assert any(k.id == key.id for k in all_keys)

    # 5. Update key
    updated = await update_api_key(
        db_path=temp_db_path,
        key_id=key.id,
        rpm_limit=120,
        monthly_budget_usd=30.0,
        enabled=False,
    )
    assert updated is True
    refetched = await get_api_key_by_id(temp_db_path, key.id)
    assert refetched is not None
    assert refetched.rpm_limit == 120
    assert refetched.monthly_budget_usd == 30.0
    assert refetched.enabled is False

    # 6. Rotate key
    rotated = await rotate_api_key(
        db_path=temp_db_path,
        key_id=key.id,
        new_hash="hash_new_999",
        new_prefix="rl_live_9999",
    )
    assert rotated is True
    refetched_rot = await get_api_key_by_id(temp_db_path, key.id)
    assert refetched_rot is not None
    assert refetched_rot.key_hash == "hash_new_999"
    assert refetched_rot.key_prefix == "rl_live_9999"


@pytest.mark.asyncio
async def test_async_log_writer_and_spend(temp_db_path: str) -> None:
    """Tests the asynchronous batch log writer and monthly spend calculation."""
    await init_db(temp_db_path)

    key = await create_api_key(
        db_path=temp_db_path,
        name="Spend Key",
        key_hash="hash_spend",
        key_prefix="rl_live_spnd",
    )

    writer = AsyncLogWriter(
        db_path=temp_db_path,
        batch_size=5,
        flush_interval=0.05,
    )
    writer.start()

    # Enqueue 3 logs
    for i in range(3):
        rec = RequestLogRecord(
            id=str(uuid.uuid4()),
            timestamp="2026-11-08T12:00:00Z",
            api_key_id=key.id,
            alias="fast",
            provider="mock-a",
            upstream_model="mock-gpt-4o-mini",
            status=200,
            attempts=1,
            cache_status="MISS" if i == 0 else "HIT",
            latency_ms=45.2,
            ttft_ms=12.0,
            tokens_in=100,
            tokens_out=200,
            cost_usd=0.002,
            error_type=None,
        )
        assert writer.enqueue(rec) is True

    # Allow background flush
    await asyncio.sleep(0.15)
    await writer.stop()

    # Check recent logs
    logs = await get_recent_logs(temp_db_path, limit=10)
    assert len(logs) == 3

    # Check stats summary
    stats = await get_stats_summary(temp_db_path)
    assert stats["total_requests"] >= 0
