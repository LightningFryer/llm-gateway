"""Distributed response cache in Redis with in-memory request coalescing (single-flight).

Caches deterministic LLM responses (temperature == 0.0) by exact-match SHA-256 hash
and collapses concurrent identical in-flight requests to eliminate cache stampedes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import redis.asyncio as aioredis

from relay.events import EventBus, RelayEvent, get_event_bus
from relay.providers.base import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    DeltaMessage,
    StreamingChoice,
)

logger = logging.getLogger("relay.cache")


def build_cache_key(
    model_alias: str,
    request: ChatCompletionRequest,
) -> str:
    """Computes a deterministic SHA-256 cache key from request parameters."""
    normalized_messages = [
        {"role": m.role, "content": m.content, "name": m.name} for m in request.messages
    ]
    key_dict = {
        "model": model_alias,
        "messages": normalized_messages,
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
        "top_p": request.top_p,
        "stop": request.stop,
    }
    canonical_json = json.dumps(key_dict, sort_keys=True, separators=(",", ":"))
    key_hash = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    return f"cache:resp:{key_hash}"


def should_cache_request(
    request: ChatCompletionRequest, cache_control_header: str | None = None
) -> bool:
    """Determines whether a request is safe to cache.

    By default, only deterministic queries (temperature == 0.0) are cached.
    Requests with 'Cache-Control: no-cache' always bypass the cache.
    """
    if cache_control_header and "no-cache" in cache_control_header.lower():
        return False
    # Safe to cache only when deterministic
    return request.temperature == 0.0


@dataclass
class CachedEntry:
    """Cached response entry stored in Redis."""

    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    raw_response: dict[str, Any]


class ResponseCache:
    """Manages Redis response caching and single-flight in-flight coalescing."""

    def __init__(
        self,
        redis_client: aioredis.Redis | None = None,
        event_bus: EventBus | None = None,
        default_ttl: int = 3600,
    ) -> None:
        self.redis = redis_client
        self.event_bus = event_bus or get_event_bus()
        self.default_ttl = default_ttl
        # In-memory single-flight tracking: cache_key -> asyncio.Event
        self._in_flight: dict[str, asyncio.Event] = {}
        # In-memory local cache fallback if Redis is unavailable
        self._mem_cache: dict[str, tuple[CachedEntry, float]] = {}

    async def get(self, cache_key: str) -> CachedEntry | None:
        """Retrieves cached response from Redis or local memory fallback."""
        if self.redis:
            try:
                raw_json = await self.redis.get(cache_key)
                if raw_json:
                    data = json.loads(raw_json)
                    await self.event_bus.publish(
                        RelayEvent(event_type="cache.hit", data={"key": cache_key})
                    )
                    return CachedEntry(**data)
            except Exception as e:
                logger.debug("Redis cache get error: %s", e)

        # In-memory fallback
        now = time.time()
        if cache_key in self._mem_cache:
            entry, expiry = self._mem_cache[cache_key]
            if now < expiry:
                await self.event_bus.publish(
                    RelayEvent(event_type="cache.hit", data={"key": cache_key})
                )
                return entry
            del self._mem_cache[cache_key]

        await self.event_bus.publish(RelayEvent(event_type="cache.miss", data={"key": cache_key}))
        return None

    async def set(
        self,
        cache_key: str,
        entry: CachedEntry,
        ttl_seconds: int | None = None,
    ) -> None:
        """Saves a response to cache with expiration TTL."""
        ttl = ttl_seconds or self.default_ttl
        payload = {
            "content": entry.content,
            "model": entry.model,
            "prompt_tokens": entry.prompt_tokens,
            "completion_tokens": entry.completion_tokens,
            "raw_response": entry.raw_response,
        }
        json_str = json.dumps(payload)

        if self.redis:
            try:
                await self.redis.set(cache_key, json_str, ex=ttl)
                return
            except Exception as e:
                logger.debug("Redis cache set error: %s", e)

        # In-memory fallback
        self._mem_cache[cache_key] = (entry, time.time() + ttl)

    def acquire_single_flight(self, cache_key: str) -> tuple[bool, asyncio.Event]:
        """Request coalescing (single-flight):

        If an identical request is already in-flight, returns (False, event).
        If caller is the first, registers the event and returns (True, event).
        """
        if cache_key in self._in_flight:
            return False, self._in_flight[cache_key]

        event = asyncio.Event()
        self._in_flight[cache_key] = event
        return True, event

    def release_single_flight(self, cache_key: str) -> None:
        """Signals and unblocks all waiting coalesced requests."""
        event = self._in_flight.pop(cache_key, None)
        if event:
            event.set()

    async def stream_cached_response(
        self,
        entry: CachedEntry,
        model_alias: str,
        request_id: str,
        pacing_delay: float = 0.005,
    ) -> AsyncIterator[str]:
        """Replays a cached completion as realistic SSE chunks for streaming clients."""
        stream_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        created_ts = int(time.time())

        # First chunk establishes role
        first_chunk = ChatCompletionChunk(
            id=stream_id,
            created=created_ts,
            model=model_alias,
            choices=[
                StreamingChoice(
                    index=0, delta=DeltaMessage(role="assistant", content=""), finish_reason=None
                )
            ],
        )
        yield f"data: {first_chunk.model_dump_json()}\n\n"

        content = entry.content or ""
        words = content.split(" ") if content else []
        for i, word in enumerate(words):
            if pacing_delay > 0:
                await asyncio.sleep(pacing_delay)
            token = word if i == 0 else " " + word
            chunk = ChatCompletionChunk(
                id=stream_id,
                created=created_ts,
                model=model_alias,
                choices=[
                    StreamingChoice(index=0, delta=DeltaMessage(content=token), finish_reason=None)
                ],
            )
            yield f"data: {chunk.model_dump_json()}\n\n"

        # Final chunk
        final_chunk = ChatCompletionChunk(
            id=stream_id,
            created=created_ts,
            model=model_alias,
            choices=[StreamingChoice(index=0, delta=DeltaMessage(), finish_reason="stop")],
        )
        yield f"data: {final_chunk.model_dump_json()}\n\n"
        yield "data: [DONE]\n\n"


# Global ResponseCache singleton
_response_cache: ResponseCache | None = None


def get_response_cache(redis_client: aioredis.Redis | None = None) -> ResponseCache:
    """Returns or initializes the global ResponseCache singleton."""
    global _response_cache
    if _response_cache is None:
        _response_cache = ResponseCache(redis_client=redis_client)
    elif redis_client is not None and _response_cache.redis is None:
        _response_cache.redis = redis_client
    return _response_cache
