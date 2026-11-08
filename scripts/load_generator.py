"""Realistic traffic and load generator for Relay LLM Gateway.

Supports:
  - Configurable RPS, duration, stream ratio, and repeat ratio (caching).
  - Traffic scenarios: 'steady', 'ramp', 'spike'.
  - Real-time periodic stats report (every 5 seconds) with p50/p95/p99 latency.
  - Benchmarks summary export.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import time
from dataclasses import dataclass, field

import httpx

# Predefined prompt pool
REPEATED_PROMPTS = [
    "Explain how a circuit breaker works in distributed systems.",
    "What is the difference between latency and throughput?",
    "Summarize the benefits of write-ahead logging (WAL) in databases.",
]

DYNAMIC_PROMPTS = [
    "Write a short haiku about network packets.",
    "What are the trade-offs of token-bucket rate limiting?",
    "How does exponential backoff prevent thundering herds?",
    "Explain the role of reverse proxies in microservice architectures.",
    "What is single-flight request coalescing?",
    "How do large language models estimate token consumption?",
    "Explain the half-open state in a circuit breaker pattern.",
]

ALIASES = ["fast", "smart", "cheap", "balanced"]


@dataclass
class LoadMetrics:
    total_sent: int = 0
    completed: int = 0
    errors: dict[int, int] = field(default_factory=dict)
    latencies_ms: list[float] = field(default_factory=list)
    cache_hits: int = 0
    total_cost_usd: float = 0.0


def calculate_percentiles(values: list[float]) -> tuple[float, float, float]:
    """Calculates p50, p95, and p99 from a list of latencies."""
    if not values:
        return 0.0, 0.0, 0.0
    sorted_vals = sorted(values)
    n = len(sorted_vals)

    def p(pct: float) -> float:
        idx = min(int(n * pct), n - 1)
        return sorted_vals[idx]

    return p(0.50), p(0.95), p(0.99)


async def send_single_request(
    client: httpx.AsyncClient,
    gateway_url: str,
    api_key: str,
    prompt: str,
    alias: str,
    stream: bool,
    metrics: LoadMetrics,
) -> None:
    metrics.total_sent += 1
    t0 = time.perf_counter()
    status_code = 500

    payload = {
        "model": alias,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,  # 0.0 allows cache hits for repeat prompts
        "stream": stream,
    }

    try:
        is_cache_hit = False
        if stream:
            async with client.stream(
                "POST",
                f"{gateway_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
                timeout=30.0,
            ) as resp:
                status_code = resp.status_code
                if resp.headers.get("X-Relay-Cache") == "HIT":
                    is_cache_hit = True
                if status_code == 200:
                    async for _ in resp.aiter_lines():
                        pass
        else:
            resp = await client.post(
                f"{gateway_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
                timeout=30.0,
            )
            status_code = resp.status_code
            if resp.headers.get("X-Relay-Cache") == "HIT":
                is_cache_hit = True

        duration_ms = (time.perf_counter() - t0) * 1000.0

        if status_code == 200:
            metrics.completed += 1
            metrics.latencies_ms.append(duration_ms)
            if is_cache_hit or duration_ms < 15.0:
                metrics.cache_hits += 1
        else:
            metrics.errors[status_code] = metrics.errors.get(status_code, 0) + 1

    except Exception:
        metrics.errors[status_code] = metrics.errors.get(status_code, 0) + 1


async def load_worker(
    scenario: str,
    base_rps: float,
    duration: float,
    stream_ratio: float,
    repeat_ratio: float,
    gateway_url: str,
    api_key: str,
    metrics: LoadMetrics,
) -> None:
    start_time = time.time()
    next_report = start_time + 5.0

    print(f"[*] Starting load generator: scenario={scenario}, base_rps={base_rps}, duration={duration}s")
    print(f"[*] Gateway: {gateway_url} | Stream ratio: {stream_ratio} | Repeat ratio: {repeat_ratio}\n")

    limits = httpx.Limits(max_keepalive_connections=50, max_connections=100)
    async with httpx.AsyncClient(limits=limits) as client:
        while True:
            now = time.time()
            elapsed = now - start_time
            if duration > 0 and elapsed >= duration:
                break

            # Calculate current target RPS based on scenario
            current_rps = base_rps
            if scenario == "ramp":
                current_rps = max(1.0, base_rps * (elapsed / max(1.0, duration)))
            elif scenario == "spike":
                # Spike to 5x traffic during 15s to 30s interval
                if 15.0 <= (elapsed % 45.0) <= 25.0:
                    current_rps = base_rps * 4.0

            # Select prompt and routing alias
            if random.random() < repeat_ratio:
                prompt = random.choice(REPEATED_PROMPTS)
            else:
                prompt = random.choice(DYNAMIC_PROMPTS) + f" (id={random.randint(1000, 9999)})"

            alias = random.choice(ALIASES)
            stream = random.random() < stream_ratio

            # Launch request asynchronously
            asyncio.create_task(
                send_single_request(
                    client=client,
                    gateway_url=gateway_url,
                    api_key=api_key,
                    prompt=prompt,
                    alias=alias,
                    stream=stream,
                    metrics=metrics,
                )
            )

            # Periodic stats printing every 5s
            if now >= next_report:
                p50, p95, p99 = calculate_percentiles(metrics.latencies_ms[-100:])
                cache_pct = (
                    (metrics.cache_hits / metrics.completed * 100.0)
                    if metrics.completed > 0
                    else 0.0
                )
                print(
                    f"[{int(elapsed):03d}s] "
                    f"Sent: {metrics.total_sent:4d} | "
                    f"Success: {metrics.completed:4d} | "
                    f"Errors: {dict(metrics.errors)} | "
                    f"p50: {p50:5.1f}ms | p95: {p95:5.1f}ms | p99: {p99:5.1f}ms | "
                    f"Cache: {cache_pct:4.1f}% | "
                    f"RPS: {current_rps:4.1f}"
                )
                next_report = now + 5.0

            # Sleep to match target RPS rate
            delay = 1.0 / max(0.1, current_rps)
            await asyncio.sleep(delay)

        # Allow in-flight requests to settle
        await asyncio.sleep(1.0)


def get_default_api_key() -> str:
    """Reads DEMO_UNLIMITED_KEY from .env.local if present, or defaults to admin secret."""
    env_local = os.path.join(os.path.dirname(__file__), "..", ".env.local")
    if os.path.exists(env_local):
        with open(env_local, encoding="utf-8") as f:
            for line in f:
                if line.startswith("DEMO_UNLIMITED_KEY="):
                    return line.split("=", 1)[1].strip()
    return "relay_admin_secret_key_12345"


def main() -> None:
    parser = argparse.ArgumentParser(description="Relay LLM Gateway Load Generator")
    parser.add_argument("--rps", type=float, default=5.0, help="Target requests per second (default: 5)")
    parser.add_argument("--duration", type=float, default=30.0, help="Run duration in seconds, 0 = infinite (default: 30)")
    parser.add_argument("--stream-ratio", type=float, default=0.7, help="Fraction of streaming requests (default: 0.7)")
    parser.add_argument("--repeat-ratio", type=float, default=0.3, help="Fraction of repeated prompts to trigger cache (default: 0.3)")
    parser.add_argument("--scenario", choices=["steady", "ramp", "spike"], default="steady", help="Load pattern scenario")
    parser.add_argument("--gateway-url", default="http://localhost:8000", help="Base URL of Relay gateway")
    parser.add_argument("--api-key", default=get_default_api_key(), help="Bearer API key")

    args = parser.parse_args()

    metrics = LoadMetrics()
    try:
        asyncio.run(
            load_worker(
                scenario=args.scenario,
                base_rps=args.rps,
                duration=args.duration,
                stream_ratio=args.stream_ratio,
                repeat_ratio=args.repeat_ratio,
                gateway_url=args.gateway_url,
                api_key=args.api_key,
                metrics=metrics,
            )
        )
    except KeyboardInterrupt:
        print("\n[!] Load test interrupted by user.")

    # Final Summary
    p50, p95, p99 = calculate_percentiles(metrics.latencies_ms)
    cache_pct = (
        (metrics.cache_hits / metrics.completed * 100.0)
        if metrics.completed > 0
        else 0.0
    )
    print("\n" + "=" * 60)
    print("LOAD TEST FINAL RESULTS")
    print("=" * 60)
    print(f"Total Sent:        {metrics.total_sent}")
    print(f"Successful:        {metrics.completed}")
    print(f"Errors by Code:    {dict(metrics.errors)}")
    print(f"Cache Hit Rate:    {cache_pct:.2f}%")
    print(f"Latency p50:       {p50:.2f} ms")
    print(f"Latency p95:       {p95:.2f} ms")
    print(f"Latency p99:       {p99:.2f} ms")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
