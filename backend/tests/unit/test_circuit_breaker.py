"""Unit tests for CircuitBreaker state machine with injectable clock."""

import pytest

from relay.circuit_breaker import CircuitBreaker, CircuitState


class MockClock:
    """Controllable clock for deterministic time manipulation in unit tests."""

    def __init__(self, initial_time: float = 1000.0) -> None:
        self.current_time = initial_time

    def __call__(self) -> float:
        return self.current_time

    def advance(self, seconds: float) -> None:
        self.current_time += seconds


@pytest.mark.asyncio
async def test_circuit_breaker_closed_state() -> None:
    """Verifies that CircuitBreaker starts in CLOSED state and permits traffic."""
    clock = MockClock()
    cb = CircuitBreaker("test-provider", clock=clock)
    assert await cb.get_state() == CircuitState.CLOSED
    assert await cb.is_allowed() is True


@pytest.mark.asyncio
async def test_circuit_breaker_trips_to_open() -> None:
    """Verifies that failures exceeding threshold trip the circuit OPEN."""
    clock = MockClock()
    cb = CircuitBreaker(
        "test-provider",
        failure_threshold=0.5,
        min_requests=4,
        cooldown_seconds=10.0,
        clock=clock,
    )

    # 2 successes, 1 failure -> 1/3 = 33% (below threshold and below min_requests)
    await cb.record_success()
    await cb.record_success()
    await cb.record_failure("error 1")
    assert await cb.get_state() == CircuitState.CLOSED

    # 2 more failures -> 3 failures out of 5 requests = 60% (> 50% threshold)
    await cb.record_failure("error 2")
    await cb.record_failure("error 3")

    assert await cb.get_state() == CircuitState.OPEN
    assert await cb.is_allowed() is False


@pytest.mark.asyncio
async def test_circuit_breaker_half_open_recovery() -> None:
    """Verifies transition from OPEN -> HALF_OPEN after cooldown, and CLOSED after probes succeed."""
    clock = MockClock()
    cb = CircuitBreaker(
        "test-provider",
        failure_threshold=0.5,
        min_requests=2,
        cooldown_seconds=15.0,
        half_open_probes=2,
        clock=clock,
    )

    # Trip OPEN
    await cb.record_failure("e1")
    await cb.record_failure("e2")
    assert await cb.get_state() == CircuitState.OPEN

    # Fast-forward clock by 10s (less than 15s cooldown) -> still OPEN
    clock.advance(10.0)
    assert await cb.get_state() == CircuitState.OPEN

    # Fast-forward clock another 6s (total 16s > 15s cooldown) -> transitions to HALF_OPEN
    clock.advance(6.0)
    assert await cb.get_state() == CircuitState.HALF_OPEN
    assert await cb.is_allowed() is True

    # Successful probes in HALF_OPEN
    await cb.record_success()
    assert await cb.get_state() == CircuitState.HALF_OPEN

    await cb.record_success()
    # After 2 successful probes, circuit closes!
    assert await cb.get_state() == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_circuit_breaker_half_open_failure_reopens() -> None:
    """Verifies that any failure during HALF_OPEN immediately reopens the breaker."""
    clock = MockClock()
    cb = CircuitBreaker(
        "test-provider",
        cooldown_seconds=10.0,
        half_open_probes=3,
        clock=clock,
    )

    # Force OPEN
    await cb.force_state(CircuitState.OPEN)
    assert await cb.get_state() == CircuitState.OPEN

    # Cooldown elapses
    clock.advance(12.0)
    assert await cb.get_state() == CircuitState.HALF_OPEN

    # A probe fails
    await cb.record_failure("probe failed")
    assert await cb.get_state() == CircuitState.OPEN
