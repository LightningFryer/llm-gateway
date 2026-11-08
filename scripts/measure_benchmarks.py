"""Script to measure specific latency benchmarks and failover recovery time."""

import asyncio
import time
import httpx

GATEWAY_URL = "http://127.0.0.1:8000"
ADMIN_KEY = "relay_admin_secret_key_12345"


async def measure_cache_speedup():
    print("--- 1. Measuring Cache Speedup ---")
    headers = {"Authorization": f"Bearer {ADMIN_KEY}"}
    prompt = f"Benchmarking deterministic prompt {time.time()}"
    payload = {
        "model": "fast",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "stream": False,
    }

    async with httpx.AsyncClient() as client:
        # First request (Cold Cache Miss)
        t0 = time.perf_counter()
        r1 = await client.post(f"{GATEWAY_URL}/v1/chat/completions", headers=headers, json=payload)
        cold_latency_ms = (time.perf_counter() - t0) * 1000.0
        r1_cache = r1.headers.get("X-Relay-Cache", "MISS")

        # Second request (Exact Match Cache Hit)
        t0 = time.perf_counter()
        r2 = await client.post(f"{GATEWAY_URL}/v1/chat/completions", headers=headers, json=payload)
        hit_latency_ms = (time.perf_counter() - t0) * 1000.0
        r2_cache = r2.headers.get("X-Relay-Cache", "MISS")

        speedup = cold_latency_ms / max(0.1, hit_latency_ms)
        print(f"Cold Request Latency: {cold_latency_ms:.2f} ms (Cache: {r1_cache})")
        print(f"Cached Request Latency: {hit_latency_ms:.2f} ms (Cache: {r2_cache})")
        print(f"Cache Speedup: {speedup:.1f}x faster")
        return cold_latency_ms, hit_latency_ms, speedup


async def measure_failover_recovery():
    print("\n--- 2. Measuring Failover Recovery Latency ---")
    headers = {"Authorization": f"Bearer {ADMIN_KEY}"}
    async with httpx.AsyncClient() as client:
        # Kill provider mock-a
        await client.post(
            f"{GATEWAY_URL}/admin/chaos",
            headers=headers,
            json={"provider": "mock-a", "is_down": True},
        )

        # Send request targeting smart alias (starts with mock-a, falls back to mock-b)
        t0 = time.perf_counter()
        resp = await client.post(
            f"{GATEWAY_URL}/v1/chat/completions",
            headers=headers,
            json={
                "model": "smart",
                "messages": [{"role": "user", "content": "Hello test failover"}],
                "stream": False,
            },
        )
        failover_latency_ms = (time.perf_counter() - t0) * 1000.0
        provider_used = resp.headers.get("X-Relay-Provider", "unknown")
        attempts = resp.headers.get("X-Relay-Attempts", "1")

        print(f"Failover Status Code: {resp.status_code}")
        print(f"Failover Latency:     {failover_latency_ms:.2f} ms")
        print(f"Provider Routed To:   {provider_used} (Expected: mock-b)")
        print(f"Attempts Count:       {attempts}")

        # Restore mock-a
        await client.post(
            f"{GATEWAY_URL}/admin/chaos",
            headers=headers,
            json={"provider": "mock-a", "is_down": False},
        )
        return failover_latency_ms, provider_used


async def main():
    await measure_cache_speedup()
    await measure_failover_recovery()


if __name__ == "__main__":
    asyncio.run(main())

