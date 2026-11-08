"""Mock LLM Provider with runtime chaos controls.

Allows the gateway and live dashboard to simulate real LLM behavior, latency,
and failure modes completely offline without real API keys.
"""

from __future__ import annotations

import asyncio
import random
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

from relay.providers.base import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    DeltaMessage,
    Provider,
    ProviderResponse,
    StreamingChoice,
)


@dataclass
class MockChaosConfig:
    """Runtime knobs for injecting chaos into this mock provider."""

    is_down: bool = False
    base_latency_ms: float = 30.0
    jitter_ms: float = 15.0
    error_rate: float = 0.0  # 0.0 to 1.0
    slow_ttft_ms: float = 0.0  # Extra time-to-first-token delay
    fail_after_tokens: int | None = None  # Mid-stream failure injection


# Sample realistic responses returned by mock models
MOCK_RESPONSES = [
    "Relay is an OpenAI-compatible gateway designed for resilient multi-provider routing and observability.",
    "Artificial intelligence systems rely on robust distributed infrastructure to handle failover and load balancing.",
    "Caching deterministic LLM queries reduces latency from seconds to milliseconds while eliminating redundant costs.",
    "Circuit breakers protect upstream dependencies by tripping open during outages and probing recovery in half-open state.",
]


class MockProvider(Provider):
    """Local controllable mock LLM that emulates realistic streaming and completion behavior."""

    def __init__(self, name: str, chaos_config: MockChaosConfig | None = None) -> None:
        super().__init__(name=name)
        self.chaos: MockChaosConfig = chaos_config or MockChaosConfig()

    def update_chaos(
        self,
        is_down: bool | None = None,
        base_latency_ms: float | None = None,
        jitter_ms: float | None = None,
        error_rate: float | None = None,
        slow_ttft_ms: float | None = None,
        fail_after_tokens: int | None = None,
    ) -> None:
        """Dynamically updates chaos settings at runtime."""
        if is_down is not None:
            self.chaos.is_down = is_down
        if base_latency_ms is not None:
            self.chaos.base_latency_ms = max(0.0, base_latency_ms)
        if jitter_ms is not None:
            self.chaos.jitter_ms = max(0.0, jitter_ms)
        if error_rate is not None:
            self.chaos.error_rate = max(0.0, min(1.0, error_rate))
        if slow_ttft_ms is not None:
            self.chaos.slow_ttft_ms = max(0.0, slow_ttft_ms)
        if fail_after_tokens is not None:
            self.chaos.fail_after_tokens = fail_after_tokens if fail_after_tokens > 0 else None

    def reset_chaos(self) -> None:
        """Resets all chaos knobs back to normal healthy defaults."""
        self.chaos = MockChaosConfig()

    def _check_pre_flight_chaos(self) -> None:
        """Checks if provider is forced down or trips an error rate before answering."""
        if self.chaos.is_down:
            raise ConnectionError(
                f"Mock provider '{self.name}' is currently forced DOWN by chaos panel."
            )
        if self.chaos.error_rate > 0.0 and random.random() < self.chaos.error_rate:
            raise RuntimeError(
                f"Mock provider '{self.name}' simulated internal 500 error (rate={self.chaos.error_rate})."
            )

    def _get_response_text(self, request: ChatCompletionRequest) -> str:
        """Generates plausible response text based on last user message."""
        last_msg = request.messages[-1].content if request.messages else "Hello"
        # Deterministic variation based on prompt hash or random
        idx = abs(hash(last_msg)) % len(MOCK_RESPONSES)
        prefix = f"[{self.name}] "
        return prefix + MOCK_RESPONSES[idx]

    async def complete(
        self,
        request: ChatCompletionRequest,
        upstream_model: str,
        timeout_seconds: float = 30.0,
    ) -> ProviderResponse:
        """Executes simulated non-streaming completion with latency and jitter."""
        start_time = time.perf_counter()
        self._check_pre_flight_chaos()

        # Simulate latency + jitter
        jitter = random.uniform(-self.chaos.jitter_ms, self.chaos.jitter_ms)
        delay = max(0.0, (self.chaos.base_latency_ms + jitter + self.chaos.slow_ttft_ms) / 1000.0)
        await asyncio.sleep(delay)

        text = self._get_response_text(request)
        prompt_len = sum(len(m.content.split()) for m in request.messages)
        prompt_tokens = max(1, int(prompt_len * 1.3))
        completion_tokens = max(1, int(len(text.split()) * 1.3))

        total_latency_ms = (time.perf_counter() - start_time) * 1000.0

        return ProviderResponse(
            content=text,
            model=upstream_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=total_latency_ms,
            ttft_ms=total_latency_ms,
        )

    async def stream(
        self,
        request: ChatCompletionRequest,
        upstream_model: str,
        timeout_seconds: float = 30.0,
    ) -> AsyncIterator[ChatCompletionChunk]:
        """Yields streaming chunks word by word with simulated pacing and mid-stream failure support."""
        self._check_pre_flight_chaos()

        # Simulate TTFT (Time To First Token) delay
        ttft_delay = (self.chaos.base_latency_ms + self.chaos.slow_ttft_ms) / 1000.0
        await asyncio.sleep(max(0.005, ttft_delay))

        stream_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        created_ts = int(time.time())

        # First chunk establishes role: assistant
        yield ChatCompletionChunk(
            id=stream_id,
            created=created_ts,
            model=upstream_model,
            choices=[
                StreamingChoice(
                    index=0,
                    delta=DeltaMessage(role="assistant", content=""),
                    finish_reason=None,
                )
            ],
        )

        text = self._get_response_text(request)
        words = text.split(" ")

        for i, word in enumerate(words):
            # Check for mid-stream failure injection
            if self.chaos.fail_after_tokens is not None and i >= self.chaos.fail_after_tokens:
                raise ConnectionResetError(
                    f"Mock provider '{self.name}' simulated mid-stream connection reset after {i} tokens."
                )

            # Realistic inter-token pacing (15-25ms)
            await asyncio.sleep(0.015)
            chunk_text = word if i == 0 else " " + word

            yield ChatCompletionChunk(
                id=stream_id,
                created=created_ts,
                model=upstream_model,
                choices=[
                    StreamingChoice(
                        index=0,
                        delta=DeltaMessage(content=chunk_text),
                        finish_reason=None,
                    )
                ],
            )

        # Final terminal chunk with finish_reason="stop"
        yield ChatCompletionChunk(
            id=stream_id,
            created=created_ts,
            model=upstream_model,
            choices=[
                StreamingChoice(
                    index=0,
                    delta=DeltaMessage(),
                    finish_reason="stop",
                )
            ],
        )


# Global Provider Registry
_providers: dict[str, Provider] = {
    "mock-a": MockProvider("mock-a"),
    "mock-b": MockProvider("mock-b"),
    "mock-c": MockProvider("mock-c"),
}


def get_provider(name: str) -> Provider:
    """Retrieves provider instance by name from the registry."""
    if name not in _providers:
        # Default to a mock provider if unrecognized
        _providers[name] = MockProvider(name)
    return _providers[name]


def get_all_providers() -> dict[str, Provider]:
    """Returns mapping of all registered providers."""
    return _providers


def register_provider(name: str, provider: Provider) -> None:
    """Registers a provider into the global registry."""
    _providers[name] = provider
