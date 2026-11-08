"""Unit tests for response cache and single-flight coalescing in cache.py."""

import asyncio

import pytest

from relay.cache import (
    CachedEntry,
    ResponseCache,
    build_cache_key,
    should_cache_request,
)
from relay.providers.base import ChatCompletionRequest, ChatMessage


def test_cache_key_determinism() -> None:
    """Verifies that identical prompts generate identical cache keys."""
    req1 = ChatCompletionRequest(
        model="smart",
        messages=[ChatMessage(role="user", content="Hello world")],
        temperature=0.0,
    )
    req2 = ChatCompletionRequest(
        model="smart",
        messages=[ChatMessage(role="user", content="Hello world")],
        temperature=0.0,
    )
    req3 = ChatCompletionRequest(
        model="smart",
        messages=[ChatMessage(role="user", content="Hello world")],
        temperature=0.7,
    )

    k1 = build_cache_key("smart", req1)
    k2 = build_cache_key("smart", req2)
    k3 = build_cache_key("smart", req3)

    assert k1 == k2
    assert k1 != k3


def test_should_cache_request_rules() -> None:
    """Verifies caching safety policies (temperature 0 and no-cache header)."""
    deterministic_req = ChatCompletionRequest(
        model="fast",
        messages=[ChatMessage(role="user", content="Test")],
        temperature=0.0,
    )
    random_req = ChatCompletionRequest(
        model="fast",
        messages=[ChatMessage(role="user", content="Test")],
        temperature=0.8,
    )

    assert should_cache_request(deterministic_req) is True
    assert should_cache_request(random_req) is False

    # Cache-Control: no-cache overrides temperature 0
    assert should_cache_request(deterministic_req, cache_control_header="no-cache") is False
    assert (
        should_cache_request(deterministic_req, cache_control_header="max-age=0, no-cache") is False
    )


@pytest.mark.asyncio
async def test_response_cache_get_set() -> None:
    """Verifies basic cache get, set, and miss handling."""
    cache = ResponseCache()
    key = "cache:resp:test12345"

    # Initial get is a miss
    assert await cache.get(key) is None

    entry = CachedEntry(
        content="Cached hello",
        model="fast",
        prompt_tokens=5,
        completion_tokens=10,
        raw_response={},
    )
    await cache.set(key, entry, ttl_seconds=60)

    cached = await cache.get(key)
    assert cached is not None
    assert cached.content == "Cached hello"


@pytest.mark.asyncio
async def test_single_flight_coalescing() -> None:
    """Verifies that multiple concurrent calls coalesce around a single leader."""
    cache = ResponseCache()
    key = "cache:resp:coalesce_key"

    # First caller is leader
    is_leader_1, event_1 = cache.acquire_single_flight(key)
    assert is_leader_1 is True

    # Second caller is follower
    is_leader_2, event_2 = cache.acquire_single_flight(key)
    assert is_leader_2 is False
    assert event_1 == event_2

    # Simulate leader task finishing
    async def leader_worker() -> None:
        await asyncio.sleep(0.05)
        entry = CachedEntry("Done", "smart", 10, 10, {})
        await cache.set(key, entry)
        cache.release_single_flight(key)

    async def follower_worker() -> str:
        await event_2.wait()
        res = await cache.get(key)
        assert res is not None
        return res.content

    _, content = await asyncio.gather(leader_worker(), follower_worker())
    assert content == "Done"
