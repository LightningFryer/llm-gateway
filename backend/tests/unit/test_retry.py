"""Unit tests for retry and jitter calculation in retry.py."""

import httpx

from relay.retry import calculate_backoff, is_retryable_error


def test_is_retryable_error() -> None:
    """Verifies that transient errors are retryable and 4xx client errors are not."""
    # Transient errors
    assert is_retryable_error(500) is True
    assert is_retryable_error(502) is True
    assert is_retryable_error(503) is True
    assert is_retryable_error(504) is True
    assert is_retryable_error(429) is True
    assert is_retryable_error(ConnectionError("Connection lost")) is True
    assert is_retryable_error(TimeoutError("Timeout")) is True
    assert is_retryable_error(httpx.ConnectError("Failed")) is True

    # Client errors
    assert is_retryable_error(400) is False
    assert is_retryable_error(401) is False
    assert is_retryable_error(403) is False
    assert is_retryable_error(404) is False
    assert is_retryable_error(422) is False


def test_calculate_backoff_jitter() -> None:
    """Verifies exponential ceiling and full jitter bounds."""
    for attempt in range(5):
        delay = calculate_backoff(attempt, base_delay_seconds=0.5, max_delay_seconds=4.0)
        ceiling = min(4.0, 0.5 * (2**attempt))
        assert 0.0 <= delay <= ceiling


def test_calculate_backoff_respects_retry_after() -> None:
    """Verifies that explicit Retry-After duration takes precedence."""
    delay = calculate_backoff(attempt=1, retry_after=2.5, max_delay_seconds=10.0)
    assert delay == 2.5

    # Capped by max_delay
    delay_capped = calculate_backoff(attempt=1, retry_after=15.0, max_delay_seconds=5.0)
    assert delay_capped == 5.0
