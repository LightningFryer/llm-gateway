"""Prometheus metrics instrumentation for Relay LLM Gateway."""

from __future__ import annotations

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

# Shared Custom Registry to avoid collision with standard python process collectors
REGISTRY = CollectorRegistry(auto_describe=True)

# 1. Request Counter
REQUESTS_TOTAL = Counter(
    "relay_requests_total",
    "Total requests handled by Relay",
    ["alias", "provider", "status"],
    registry=REGISTRY,
)

# 2. Latency Histograms
REQUEST_LATENCY = Histogram(
    "relay_request_duration_seconds",
    "End-to-end request duration in seconds",
    ["alias", "provider"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    registry=REGISTRY,
)

TTFT_HISTOGRAM = Histogram(
    "relay_time_to_first_token_seconds",
    "Time to first token (TTFT) in seconds for streaming completions",
    ["alias", "provider"],
    buckets=(0.01, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0),
    registry=REGISTRY,
)

# 3. Active Streaming Connections Gauge
ACTIVE_STREAMS = Gauge(
    "relay_active_streams",
    "Number of active SSE streaming connections",
    registry=REGISTRY,
)

# 4. Cache Metrics
CACHE_EVENTS = Counter(
    "relay_cache_events_total",
    "Total exact-match response cache lookups",
    ["status"],  # 'hit', 'miss', 'bypass'
    registry=REGISTRY,
)

# 5. Resilience Counters
RETRIES_TOTAL = Counter(
    "relay_retries_total",
    "Total provider retries attempted",
    ["provider"],
    registry=REGISTRY,
)

FAILOVERS_TOTAL = Counter(
    "relay_failovers_total",
    "Total transparent failover events",
    ["alias", "from_provider"],
    registry=REGISTRY,
)

CIRCUIT_STATE = Gauge(
    "relay_circuit_state",
    "Current Circuit Breaker state (0=CLOSED, 1=HALF_OPEN, 2=OPEN)",
    ["provider"],
    registry=REGISTRY,
)

RATE_LIMIT_REJECTIONS = Counter(
    "relay_ratelimit_rejections_total",
    "Rate limit 429 rejections",
    ["type"],  # 'rpm', 'tpm'
    registry=REGISTRY,
)

# 6. Usage & Cost Accounting
TOKENS_TOTAL = Counter(
    "relay_tokens_total",
    "Total LLM tokens processed",
    ["direction"],  # 'prompt', 'completion'
    registry=REGISTRY,
)

COST_TOTAL_USD = Counter(
    "relay_cost_usd_total",
    "Cumulative estimated spend in USD",
    registry=REGISTRY,
)


def get_metrics_content() -> tuple[bytes, str]:
    """Generates serialized Prometheus metrics and corresponding MIME content type."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
