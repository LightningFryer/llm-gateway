"""Unit tests for TokenBucket and SlidingWindow rate limiters."""

import fakeredis.aioredis as fake_aioredis
import pytest

from relay.ratelimit import (
    MemoryRateLimiter,
    SlidingWindowRateLimiter,
    TokenBucketRateLimiter,
)


@pytest.mark.asyncio
async def test_memory_rate_limiter() -> None:
    """Verifies in-memory rate limiter token consumption and rejection."""
    limiter = MemoryRateLimiter()

    # Limit = 3 requests
    r1 = await limiter.check_and_consume("k1", "rpm", limit=3, cost=1)
    assert r1.allowed is True
    assert r1.remaining == 2

    r2 = await limiter.check_and_consume("k1", "rpm", limit=3, cost=2)
    assert r2.allowed is True
    assert r2.remaining == 0

    # 4th request rejected
    r3 = await limiter.check_and_consume("k1", "rpm", limit=3, cost=1)
    assert r3.allowed is False
    assert r3.retry_after >= 1


@pytest.mark.asyncio
async def test_token_bucket_redis(fake_redis: fake_aioredis.FakeRedis) -> None:
    """Verifies Redis Token Bucket with optimistic locking (no Lua)."""
    limiter = TokenBucketRateLimiter(redis_client=fake_redis)

    # Consume 2 units from bucket of capacity 5
    res1 = await limiter.check_and_consume("key_tb", "rpm", limit=5, cost=2)
    assert res1.allowed is True
    assert res1.remaining == 3

    # Consume another 3 units
    res2 = await limiter.check_and_consume("key_tb", "rpm", limit=5, cost=3)
    assert res2.allowed is True
    assert res2.remaining == 0

    # Bucket is empty, request rejected
    res3 = await limiter.check_and_consume("key_tb", "rpm", limit=5, cost=1)
    assert res3.allowed is False
    assert res3.retry_after >= 1


@pytest.mark.asyncio
async def test_sliding_window_redis(fake_redis: fake_aioredis.FakeRedis) -> None:
    """Verifies Redis Sliding Window Log with sorted set pipelines (no Lua)."""
    limiter = SlidingWindowRateLimiter(redis_client=fake_redis)

    # Limit = 4 requests
    for _ in range(4):
        res = await limiter.check_and_consume("key_sw", "rpm", limit=4, cost=1)
        assert res.allowed is True

    # 5th request should be rejected
    res_overflow = await limiter.check_and_consume("key_sw", "rpm", limit=4, cost=1)
    assert res_overflow.allowed is False
    assert res_overflow.retry_after >= 1
