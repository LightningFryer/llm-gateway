"""Redis-backed 3-State Circuit Breaker with In-Memory Fallback.

Tracks failure rates across rolling time windows and prevents cascading outages
by opening failing circuits and allowing gradual recovery in half-open state.
All state transitions emit events to the EventBus.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from enum import Enum

import redis.asyncio as aioredis

from relay.events import EventBus, RelayEvent, get_event_bus

logger = logging.getLogger("relay.circuit_breaker")


class CircuitState(str, Enum):
    """The three canonical states of a circuit breaker."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    """Manages failure thresholds, recovery timeouts, and probe tracking for an upstream target.

    Can store state in Redis (for multi-replica consistency) with automatic
    fallback to local in-memory dictionaries.
    """

    def __init__(
        self,
        provider_name: str,
        failure_threshold: float = 0.5,
        min_requests: int = 5,
        cooldown_seconds: float = 10.0,
        half_open_probes: int = 3,
        window_seconds: float = 30.0,
        redis_client: aioredis.Redis | None = None,
        event_bus: EventBus | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.provider = provider_name
        self.failure_threshold = failure_threshold
        self.min_requests = min_requests
        self.cooldown_seconds = cooldown_seconds
        self.half_open_probes = half_open_probes
        self.window_seconds = window_seconds
        self.redis = redis_client
        self.event_bus = event_bus or get_event_bus()
        self.clock = clock

        # Redis Keys
        self.k_state = f"cb:{provider_name}:state"
        self.k_failures = f"cb:{provider_name}:failures"
        self.k_requests = f"cb:{provider_name}:requests"
        self.k_open_time = f"cb:{provider_name}:open_time"
        self.k_probes = f"cb:{provider_name}:probes"

        # Local In-Memory Fallback State (used if Redis is unavailable or in unit tests)
        self._mem_state = CircuitState.CLOSED
        self._mem_failures: list[float] = []
        self._mem_requests: list[float] = []
        self._mem_open_time: float = 0.0
        self._mem_probes_success: int = 0
        self._mem_probes_active: int = 0

    async def get_state(self) -> CircuitState:
        """Reads current circuit state, checking if cooldown has elapsed in OPEN state."""
        now = self.clock()

        if self.redis:
            try:
                state_raw = await self.redis.get(self.k_state)
                curr_state = CircuitState(state_raw) if state_raw else CircuitState.CLOSED

                if curr_state == CircuitState.OPEN:
                    open_time_raw = await self.redis.get(self.k_open_time)
                    open_time = float(open_time_raw) if open_time_raw else 0.0
                    if (now - open_time) >= self.cooldown_seconds:
                        await self._transition_to(CircuitState.HALF_OPEN)
                        return CircuitState.HALF_OPEN

                return curr_state
            except Exception as e:
                logger.debug("Redis circuit breaker read error, using memory: %s", e)

        # In-memory evaluation
        if self._mem_state == CircuitState.OPEN:
            if (now - self._mem_open_time) >= self.cooldown_seconds:
                await self._transition_to(CircuitState.HALF_OPEN)
        return self._mem_state

    async def is_allowed(self) -> bool:
        """Returns True if the circuit permits a request to proceed."""
        state = await self.get_state()
        if state == CircuitState.CLOSED:
            return True

        if state == CircuitState.OPEN:
            return False

        # In HALF_OPEN, allow limited probe requests
        if self.redis:
            try:
                active_probes = await self.redis.incr(self.k_probes)
                if active_probes <= self.half_open_probes:
                    return True
                return False
            except Exception:
                pass

        if self._mem_probes_active < self.half_open_probes:
            self._mem_probes_active += 1
            return True
        return False

    async def record_success(self) -> None:
        """Records a successful request completion."""
        state = await self.get_state()
        now = self.clock()

        if state == CircuitState.HALF_OPEN:
            # Successful probe in HALF_OPEN
            if self.redis:
                try:
                    successes = await self.redis.incr(f"{self.k_probes}:success")
                    if successes >= self.half_open_probes:
                        await self._transition_to(CircuitState.CLOSED)
                    return
                except Exception:
                    pass

            self._mem_probes_success += 1
            if self._mem_probes_success >= self.half_open_probes:
                await self._transition_to(CircuitState.CLOSED)
            return

        if state == CircuitState.CLOSED:
            # Add to rolling request window
            if self.redis:
                try:
                    pipe = self.redis.pipeline()
                    pipe.zadd(self.k_requests, {str(uuid.uuid4()): now})
                    pipe.zremrangebyscore(self.k_requests, 0, now - self.window_seconds)
                    pipe.expire(self.k_requests, int(self.window_seconds * 2))
                    await pipe.execute()
                    return
                except Exception:
                    pass

            self._mem_requests.append(now)
            cutoff = now - self.window_seconds
            self._mem_requests = [t for t in self._mem_requests if t >= cutoff]

    async def record_failure(self, error: Exception | str | None = None) -> None:
        """Records a failed request and evaluates if the circuit should trip OPEN."""
        state = await self.get_state()
        now = self.clock()
        err_msg = str(error) if error else "Unknown failure"

        if state == CircuitState.HALF_OPEN:
            # Any failure during half-open probing immediately reopens the circuit
            logger.warning(
                "Probe failed during HALF_OPEN for provider '%s': %s. Reopening circuit.",
                self.provider,
                err_msg,
            )
            await self._transition_to(CircuitState.OPEN)
            return

        if state == CircuitState.CLOSED:
            req_count = 0
            fail_count = 0

            if self.redis:
                try:
                    pipe = self.redis.pipeline()
                    item_id = str(uuid.uuid4())
                    pipe.zadd(self.k_requests, {item_id: now})
                    pipe.zadd(self.k_failures, {item_id: now})
                    cutoff = now - self.window_seconds
                    pipe.zremrangebyscore(self.k_requests, 0, cutoff)
                    pipe.zremrangebyscore(self.k_failures, 0, cutoff)
                    pipe.zcard(self.k_requests)
                    pipe.zcard(self.k_failures)
                    results = await pipe.execute()
                    req_count = results[4]
                    fail_count = results[5]
                except Exception as e:
                    logger.debug("Redis failure recording error: %s", e)
                    req_count = 0

            if req_count == 0:
                self._mem_requests.append(now)
                self._mem_failures.append(now)
                cutoff = now - self.window_seconds
                self._mem_requests = [t for t in self._mem_requests if t >= cutoff]
                self._mem_failures = [t for t in self._mem_failures if t >= cutoff]
                req_count = len(self._mem_requests)
                fail_count = len(self._mem_failures)

            if req_count >= self.min_requests:
                rate = fail_count / float(req_count)
                if rate >= self.failure_threshold:
                    logger.warning(
                        "Provider '%s' failure rate %.1f%% (%d/%d) crossed threshold %.1f%%. Tripping circuit OPEN.",
                        self.provider,
                        rate * 100,
                        fail_count,
                        req_count,
                        self.failure_threshold * 100,
                    )
                    await self._transition_to(CircuitState.OPEN)

    async def force_state(self, state: CircuitState) -> None:
        """Forces the circuit breaker into a specific state (used by chaos controls)."""
        await self._transition_to(state)

    async def _transition_to(self, new_state: CircuitState) -> None:
        """Executes state transition, resets counters, and publishes event."""
        now = self.clock()
        old_state = self._mem_state

        if self.redis:
            try:
                pipe = self.redis.pipeline()
                pipe.set(self.k_state, new_state.value)
                if new_state == CircuitState.OPEN:
                    pipe.set(self.k_open_time, str(now))
                    pipe.delete(self.k_probes)
                    pipe.delete(f"{self.k_probes}:success")
                elif new_state == CircuitState.HALF_OPEN:
                    pipe.set(self.k_probes, 0)
                    pipe.set(f"{self.k_probes}:success", 0)
                elif new_state == CircuitState.CLOSED:
                    pipe.delete(self.k_failures)
                    pipe.delete(self.k_requests)
                    pipe.delete(self.k_probes)
                    pipe.delete(f"{self.k_probes}:success")
                await pipe.execute()
            except Exception as e:
                logger.debug("Redis state transition error: %s", e)

        # Update in-memory state
        self._mem_state = new_state
        if new_state == CircuitState.OPEN:
            self._mem_open_time = now
            self._mem_probes_active = 0
            self._mem_probes_success = 0
        elif new_state == CircuitState.HALF_OPEN:
            self._mem_probes_active = 0
            self._mem_probes_success = 0
        elif new_state == CircuitState.CLOSED:
            self._mem_failures.clear()
            self._mem_requests.clear()
            self._mem_probes_active = 0
            self._mem_probes_success = 0

        # Emit event
        event_name = f"circuit.{new_state.value.lower()}"
        event = RelayEvent(
            event_type=event_name,
            data={
                "provider": self.provider,
                "from_state": old_state.value,
                "to_state": new_state.value,
                "timestamp": now,
            },
        )
        await self.event_bus.publish(event)
        logger.info("CircuitBreaker[%s]: %s -> %s", self.provider, old_state.value, new_state.value)


# Global CircuitBreaker registry
_circuit_breakers: dict[str, CircuitBreaker] = {}


def get_circuit_breaker(
    provider_name: str,
    redis_client: aioredis.Redis | None = None,
    failure_threshold: float = 0.5,
    min_requests: int = 5,
    cooldown_seconds: float = 10.0,
    half_open_probes: int = 3,
    clock: Callable[[], float] = time.time,
) -> CircuitBreaker:
    """Returns or creates the circuit breaker instance for a given provider."""
    global _circuit_breakers
    if provider_name not in _circuit_breakers:
        _circuit_breakers[provider_name] = CircuitBreaker(
            provider_name=provider_name,
            failure_threshold=failure_threshold,
            min_requests=min_requests,
            cooldown_seconds=cooldown_seconds,
            half_open_probes=half_open_probes,
            redis_client=redis_client,
            clock=clock,
        )
    elif redis_client is not None and _circuit_breakers[provider_name].redis is None:
        _circuit_breakers[provider_name].redis = redis_client
    return _circuit_breakers[provider_name]


def get_all_circuit_breakers() -> dict[str, CircuitBreaker]:
    """Returns mapping of all instantiated circuit breakers."""
    return _circuit_breakers
