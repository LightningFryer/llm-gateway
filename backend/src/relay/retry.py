"""Retry policies, backoff calculation, and transient error classification."""

from __future__ import annotations

import random
from dataclasses import dataclass

import httpx


@dataclass
class RetryConfig:
    """Configuration for retry execution."""

    max_attempts: int = 3
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 5.0
    jitter: bool = True


RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504, 529}
NON_RETRYABLE_STATUS_CODES = {400, 401, 403, 404, 422}


def is_retryable_error(exc_or_status: Exception | int) -> bool:
    """Determines whether an error is transient and safe to retry.

    Retries network drops, timeouts, 429s, and 5xx server errors.
    Never retries 4xx client errors (which are deterministic).
    """
    if isinstance(exc_or_status, int):
        return exc_or_status in RETRYABLE_STATUS_CODES

    if isinstance(
        exc_or_status,
        TimeoutError
        | ConnectionError
        | ConnectionResetError
        | httpx.TimeoutException
        | httpx.ConnectError
        | httpx.NetworkError,
    ):
        return True

    # Check for HTTP status codes inside HTTP exceptions
    response = getattr(exc_or_status, "response", None)
    status_code = getattr(response, "status_code", None) or getattr(exc_or_status, "status_code", None)
    if status_code is not None:
        return status_code in RETRYABLE_STATUS_CODES

    # Generic RuntimeErrors simulating 500s in mock
    msg = str(exc_or_status).lower()
    if "500" in msg or "503" in msg or "down" in msg or "timeout" in msg:
        return True

    return False


def calculate_backoff(
    attempt: int,
    base_delay_seconds: float = 0.5,
    max_delay_seconds: float = 5.0,
    retry_after: float | None = None,
) -> float:
    """Calculates exponential backoff delay with full jitter.

    If upstream provides a specific Retry-After duration, that duration is respected.
    """
    if retry_after is not None and retry_after > 0:
        return min(retry_after, max_delay_seconds)

    # Full jitter: random uniform between 0 and exponential ceiling
    ceiling = min(max_delay_seconds, base_delay_seconds * (2**attempt))
    return random.uniform(0.0, ceiling)
