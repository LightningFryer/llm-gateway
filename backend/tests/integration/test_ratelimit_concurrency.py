"""Concurrency tests proving rate limits hold across multiple app instances sharing Redis."""

import asyncio

import fakeredis.aioredis as fake_aioredis
import pytest

from relay.ratelimit import SlidingWindowRateLimiter, TokenBucketRateLimiter


@pytest.mark.asyncio
async def test_token_bucket_concurrency_across_instances(
    fake_redis: fake_aioredis.FakeRedis,
) -> None:
    """Proves that Token Bucket strictly enforces limit under high concurrent contention from 2 instances."""
    # Simulate two independent gateway replicas sharing the same Redis backend
    instance_1 = TokenBucketRateLimiter(redis_client=fake_redis)
    instance_2 = TokenBucketRateLimiter(redis_client=fake_redis)

    limit = 20
    total_concurrent_requests = 60

    async def send_request(instance: TokenBucketRateLimiter) -> bool:
        res = await instance.check_and_consume(
            key_id="tenant_concurrency_test",
            limit_type="rpm",
            limit=limit,
            cost=1,
            window_seconds=60,
        )
        return res.allowed

    # Fire 60 requests simultaneously, alternating between instance 1 and instance 2
    tasks = [
        send_request(instance_1 if i % 2 == 0 else instance_2)
        for i in range(total_concurrent_requests)
    ]
    results = await asyncio.gather(*tasks)

    allowed_count = sum(1 for r in results if r is True)
    denied_count = sum(1 for r in results if r is False)

    # Exactly 20 allowed, 40 rejected
    assert allowed_count == limit
    assert denied_count == (total_concurrent_requests - limit)


@pytest.mark.asyncio
async def test_sliding_window_concurrency_across_instances(
    fake_redis: fake_aioredis.FakeRedis,
) -> None:
    """Proves that Sliding Window strictly enforces limit under high concurrent contention from 2 instances."""
    instance_1 = SlidingWindowRateLimiter(redis_client=fake_redis)
    instance_2 = SlidingWindowRateLimiter(redis_client=fake_redis)

    limit = 15
    total_concurrent_requests = 50

    async def send_request(instance: SlidingWindowRateLimiter) -> bool:
        res = await instance.check_and_consume(
            key_id="tenant_sw_concurrency_test",
            limit_type="rpm",
            limit=limit,
            cost=1,
            window_seconds=60,
        )
        return res.allowed

    tasks = [
        send_request(instance_1 if i % 2 == 0 else instance_2)
        for i in range(total_concurrent_requests)
    ]
    results = await asyncio.gather(*tasks)

    allowed_count = sum(1 for r in results if r is True)
    denied_count = sum(1 for r in results if r is False)

    # Exactly 15 allowed, 35 rejected
    assert allowed_count == limit
    assert denied_count == (total_concurrent_requests - limit)
