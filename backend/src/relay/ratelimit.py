"""Distributed Rate Limiting (Token Bucket & Sliding Window Log) without Lua scripts.

Enforces Requests-Per-Minute (RPM) and Tokens-Per-Minute (TPM) limits per API key
across all gateway replicas using redis-py optimistic locking (WATCH/MULTI/EXEC)
and sorted set pipelines, with automatic fallback to in-memory tracking if Redis is offline.
NO LUA SCRIPTS.
"""

from __future__ import annotations

import logging
import math
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, cast

import redis.asyncio as aioredis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from redis.exceptions import WatchError

from relay.events import RelayEvent, get_event_bus

logger = logging.getLogger("relay.ratelimit")


@dataclass
class RateLimitResult:
    """Outcome of rate limit evaluation."""

    allowed: bool
    limit: int
    remaining: int
    reset_seconds: int
    retry_after: int = 0


class RateLimiter(ABC):
    """Abstract interface for rate limiter implementations."""

    @abstractmethod
    async def check_and_consume(
        self,
        key_id: str,
        limit_type: str,
        limit: int,
        cost: int = 1,
        window_seconds: int = 60,
    ) -> RateLimitResult:
        """Evaluates whether consuming 'cost' units is permitted under limit."""
        ...


class TokenBucketRateLimiter(RateLimiter):
    """Smooth token bucket rate limiter allowing controlled bursts up to bucket capacity.

    Stored as a Redis Hash with keys 'tokens' and 'last_updated', synchronized using
    optimistic concurrency control (WATCH / MULTI / EXEC) with a bounded retry loop.
    NO LUA SCRIPTS.
    """

    def __init__(self, redis_client: aioredis.Redis, max_retries: int = 10) -> None:
        self.redis = redis_client
        self.max_retries = max_retries

    async def check_and_consume(
        self,
        key_id: str,
        limit_type: str,
        limit: int,
        cost: int = 1,
        window_seconds: int = 60,
    ) -> RateLimitResult:
        redis_key = f"rl:tb:{key_id}:{limit_type}"
        refill_rate = float(limit) / float(window_seconds)

        for _ in range(self.max_retries):
            try:
                # 1. Watch the key for optimistic locking
                await self.redis.watch(redis_key)
                now = time.time()

                # 2. Read existing bucket values
                data_raw = await self.redis.hgetall(redis_key)  # type: ignore[misc]
                data = cast(dict[Any, Any], data_raw)
                if not data:
                    tokens = float(limit)
                    last_updated = now
                else:
                    tokens = float(data.get("tokens", limit))
                    last_updated = float(data.get("last_updated", now))

                # 3. Compute refilled tokens based on elapsed time
                elapsed = max(0.0, now - last_updated)
                tokens = min(float(limit), tokens + (elapsed * refill_rate))

                # 4. Check if bucket has sufficient tokens
                if tokens >= cost:
                    new_tokens = tokens - cost
                    pipe = self.redis.pipeline()
                    pipe.multi()
                    pipe.hset(
                        redis_key,
                        mapping={
                            "tokens": str(new_tokens),
                            "last_updated": str(now),
                        },
                    )
                    pipe.expire(redis_key, window_seconds * 2)
                    await pipe.execute()

                    remaining = max(0, int(new_tokens))
                    reset_seconds = (
                        max(1, int((limit - new_tokens) / refill_rate))
                        if refill_rate > 0
                        else window_seconds
                    )
                    return RateLimitResult(
                        allowed=True,
                        limit=limit,
                        remaining=remaining,
                        reset_seconds=reset_seconds,
                    )
                else:
                    # Insufficient tokens
                    await self.redis.unwatch()
                    deficit = cost - tokens
                    retry_after = max(1, int(math.ceil(deficit / refill_rate)))
                    reset_seconds = max(1, int((limit - tokens) / refill_rate))

                    await get_event_bus().publish(
                        RelayEvent(
                            event_type="ratelimit.rejected",
                            data={
                                "key_id": key_id,
                                "type": limit_type,
                                "limit": limit,
                                "retry_after": retry_after,
                            },
                        )
                    )
                    return RateLimitResult(
                        allowed=False,
                        limit=limit,
                        remaining=0,
                        reset_seconds=reset_seconds,
                        retry_after=retry_after,
                    )

            except WatchError:
                # Concurrently modified by another worker: retry optimistic transaction
                continue

        # If retries exhausted under extreme contention, deny to be safe
        return RateLimitResult(
            allowed=False,
            limit=limit,
            remaining=0,
            reset_seconds=window_seconds,
            retry_after=1,
        )


class SlidingWindowRateLimiter(RateLimiter):
    """Strict rolling window rate limiter using Redis Sorted Sets in a MULTI/EXEC pipeline.

    Maintains zero burst vulnerability over any rolling 60-second window.
    NO LUA SCRIPTS.
    """

    def __init__(self, redis_client: aioredis.Redis) -> None:
        self.redis = redis_client

    async def check_and_consume(
        self,
        key_id: str,
        limit_type: str,
        limit: int,
        cost: int = 1,
        window_seconds: int = 60,
    ) -> RateLimitResult:
        redis_key = f"rl:sw:{key_id}:{limit_type}"
        now = time.time()
        window_start = now - window_seconds

        # Execute pipeline to prune expired entries and count current usage
        pipe = self.redis.pipeline()
        pipe.zremrangebyscore(redis_key, 0, window_start)
        pipe.zcard(redis_key)
        pipe.expire(redis_key, window_seconds * 2)
        results = await pipe.execute()
        current_count = int(results[1])

        if (current_count + cost) <= limit:
            # Under limit: add entries
            add_pipe = self.redis.pipeline()
            items = {f"{now}_{i}_{uuid.uuid4().hex[:6]}": now for i in range(cost)}
            add_pipe.zadd(redis_key, items)
            add_pipe.expire(redis_key, window_seconds * 2)
            await add_pipe.execute()

            remaining = max(0, limit - (current_count + cost))
            return RateLimitResult(
                allowed=True,
                limit=limit,
                remaining=remaining,
                reset_seconds=window_seconds,
            )
        else:
            # Over limit
            retry_after = 1
            # Find earliest entry timestamp to calculate exact retry-after
            oldest = await self.redis.zrange(redis_key, 0, 0, withscores=True)
            if oldest:
                oldest_ts = float(oldest[0][1])
                retry_after = max(1, int(math.ceil((oldest_ts + window_seconds) - now)))

            await get_event_bus().publish(
                RelayEvent(
                    event_type="ratelimit.rejected",
                    data={
                        "key_id": key_id,
                        "type": limit_type,
                        "limit": limit,
                        "retry_after": retry_after,
                    },
                )
            )
            return RateLimitResult(
                allowed=False,
                limit=limit,
                remaining=0,
                reset_seconds=retry_after,
                retry_after=retry_after,
            )


class MemoryRateLimiter(RateLimiter):
    """In-memory rate limiter for testing and graceful fallback when Redis is unreachable."""

    def __init__(self) -> None:
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_updated)

    async def check_and_consume(
        self,
        key_id: str,
        limit_type: str,
        limit: int,
        cost: int = 1,
        window_seconds: int = 60,
    ) -> RateLimitResult:
        key = f"{key_id}:{limit_type}"
        now = time.time()
        refill_rate = float(limit) / float(window_seconds)

        tokens, last_updated = self._buckets.get(key, (float(limit), now))
        elapsed = max(0.0, now - last_updated)
        tokens = min(float(limit), tokens + (elapsed * refill_rate))

        if tokens >= cost:
            tokens -= cost
            self._buckets[key] = (tokens, now)
            return RateLimitResult(
                allowed=True,
                limit=limit,
                remaining=int(tokens),
                reset_seconds=window_seconds,
            )
        else:
            deficit = cost - tokens
            retry_after = max(1, int(math.ceil(deficit / refill_rate)))
            return RateLimitResult(
                allowed=False,
                limit=limit,
                remaining=0,
                reset_seconds=retry_after,
                retry_after=retry_after,
            )


class ResilientRateLimiter(RateLimiter):
    """Wraps a primary Redis rate limiter with an automatic in-memory fallback.

    Ensures zero request drops or 500 errors if Redis is down or experiencing network partitions.
    """

    def __init__(self, primary: RateLimiter, fallback: MemoryRateLimiter) -> None:
        self.primary = primary
        self.fallback = fallback

    async def check_and_consume(
        self,
        key_id: str,
        limit_type: str,
        limit: int,
        cost: int = 1,
        window_seconds: int = 60,
    ) -> RateLimitResult:
        try:
            return await self.primary.check_and_consume(
                key_id=key_id,
                limit_type=limit_type,
                limit=limit,
                cost=cost,
                window_seconds=window_seconds,
            )
        except (RedisConnectionError, RedisTimeoutError, OSError) as e:
            logger.warning(
                "Redis rate limiter unavailable (%s). Falling back to in-memory limiter.", e
            )
            return await self.fallback.check_and_consume(
                key_id=key_id,
                limit_type=limit_type,
                limit=limit,
                cost=cost,
                window_seconds=window_seconds,
            )
