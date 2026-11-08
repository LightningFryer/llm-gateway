"""Internal Event Bus for publishing and subscribing to system events.

Supports local in-process asynchronous subscriptions as well as cross-replica
broadcasts via Redis Pub/Sub.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as aioredis

logger = logging.getLogger("relay.events")

REDIS_EVENT_CHANNEL = "relay:events"


@dataclass
class RelayEvent:
    """Structured event emitted across Relay."""

    event_type: str
    data: dict[str, Any]
    id: str = ""
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = str(uuid.uuid4())
        if not self.timestamp:
            self.timestamp = datetime.now(UTC).isoformat()

    def to_dict(self) -> dict[str, Any]:
        """Converts event to dictionary."""
        return asdict(self)

    def to_json(self) -> str:
        """Serializes event to JSON string."""
        return json.dumps(self.to_dict())


class EventBus:
    """Pub/sub event bus supporting in-memory fanout and optional Redis pub/sub."""

    def __init__(self, redis_url: str | None = None) -> None:
        self.redis_url = redis_url
        self._local_subscribers: set[asyncio.Queue[RelayEvent]] = set()
        self._redis_client: aioredis.Redis | None = None
        self._redis_listener_task: asyncio.Task[None] | None = None
        self._is_running = False

    async def start(self) -> None:
        """Connects to Redis pub/sub listener if Redis URL is configured."""
        self._is_running = True
        if self.redis_url:
            try:
                client = aioredis.from_url(self.redis_url, decode_responses=True)
                await client.ping()
                self._redis_client = client
                self._redis_listener_task = asyncio.create_task(self._listen_redis_channel())
                logger.info("EventBus connected to Redis Pub/Sub channel '%s'", REDIS_EVENT_CHANNEL)
            except Exception as e:
                logger.warning(
                    "EventBus failed to connect to Redis (%s). Operating in-memory only.", e
                )
                self._redis_client = None

    async def stop(self) -> None:
        """Stops listener and cleans up connections."""
        self._is_running = False
        if self._redis_listener_task:
            self._redis_listener_task.cancel()
            self._redis_listener_task = None
        if self._redis_client:
            await self._redis_client.aclose()
            self._redis_client = None

    async def publish(self, event: RelayEvent) -> None:
        """Publishes an event to all local subscribers and broadcasts to Redis."""
        # 1. Local in-process delivery
        dead_queues: list[asyncio.Queue[RelayEvent]] = []
        for q in list(self._local_subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead_queues.append(q)

        for q in dead_queues:
            self._local_subscribers.discard(q)

        # 2. Redis pub/sub broadcast to other replicas
        if self._redis_client:
            try:
                await self._redis_client.publish(REDIS_EVENT_CHANNEL, event.to_json())
            except Exception as e:
                logger.debug("Failed to publish event to Redis: %s", e)

    def subscribe(self, maxsize: int = 500) -> asyncio.Queue[RelayEvent]:
        """Creates an in-memory queue that receives all published events."""
        q: asyncio.Queue[RelayEvent] = asyncio.Queue(maxsize=maxsize)
        self._local_subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[RelayEvent]) -> None:
        """Removes a subscriber queue."""
        self._local_subscribers.discard(q)

    async def stream_events(self) -> AsyncIterator[RelayEvent]:
        """Generator yielding events for an SSE client."""
        q = self.subscribe()
        try:
            while self._is_running:
                event = await q.get()
                yield event
                q.task_done()
        except asyncio.CancelledError:
            pass
        finally:
            self.unsubscribe(q)

    async def _listen_redis_channel(self) -> None:
        """Background task listening for Redis pub/sub messages from other replicas."""
        if not self._redis_client:
            return
        pubsub = self._redis_client.pubsub()
        await pubsub.subscribe(REDIS_EVENT_CHANNEL)

        try:
            while self._is_running:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message and message.get("type") == "message":
                    raw_data = message.get("data")
                    if isinstance(raw_data, str):
                        try:
                            parsed = json.loads(raw_data)
                            event = RelayEvent(
                                id=parsed.get("id", ""),
                                event_type=parsed.get("event_type", "unknown"),
                                timestamp=parsed.get("timestamp", ""),
                                data=parsed.get("data", {}),
                            )
                            # Distribute to local subscribers
                            for q in list(self._local_subscribers):
                                try:
                                    q.put_nowait(event)
                                except asyncio.QueueFull:
                                    pass
                        except Exception as e:
                            logger.error("Failed to parse Redis event message: %s", e)
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("Redis Pub/Sub listener disconnected: %s", e)
        finally:
            await pubsub.unsubscribe(REDIS_EVENT_CHANNEL)
            await pubsub.aclose()


# Global EventBus singleton
_event_bus: EventBus | None = None


def get_event_bus(redis_url: str | None = None) -> EventBus:
    """Returns or initializes the global EventBus singleton."""
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus(redis_url=redis_url)
    return _event_bus
