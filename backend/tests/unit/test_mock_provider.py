"""Unit tests for MockProvider with chaos controls."""

import pytest

from relay.providers.base import ChatCompletionRequest, ChatMessage
from relay.providers.mock import MockChaosConfig, MockProvider


@pytest.mark.asyncio
async def test_mock_provider_complete_normal() -> None:
    """Verifies that MockProvider completes request with plausible text and usage."""
    provider = MockProvider("test-mock")
    req = ChatCompletionRequest(
        model="fast",
        messages=[ChatMessage(role="user", content="Explain distributed gateways")],
    )

    resp = await provider.complete(req, upstream_model="mock-gpt-4o-mini")
    assert resp.content.startswith("[test-mock]")
    assert resp.prompt_tokens > 0
    assert resp.completion_tokens > 0
    assert resp.latency_ms >= 0


@pytest.mark.asyncio
async def test_mock_provider_stream_normal() -> None:
    """Verifies that MockProvider yields streaming chunks ending with stop."""
    provider = MockProvider("test-mock")
    req = ChatCompletionRequest(
        model="fast",
        messages=[ChatMessage(role="user", content="Stream this")],
        stream=True,
    )

    chunks = []
    async for chunk in provider.stream(req, upstream_model="mock-gpt-4o-mini"):
        chunks.append(chunk)

    assert len(chunks) >= 3
    # First chunk has assistant role
    assert chunks[0].choices[0].delta.role == "assistant"
    # Final chunk has stop reason
    assert chunks[-1].choices[0].finish_reason == "stop"


@pytest.mark.asyncio
async def test_mock_provider_forced_down() -> None:
    """Verifies that MockProvider raises ConnectionError when forced down."""
    provider = MockProvider("test-mock", MockChaosConfig(is_down=True))
    req = ChatCompletionRequest(
        model="fast",
        messages=[ChatMessage(role="user", content="Hello")],
    )

    with pytest.raises(ConnectionError, match="forced DOWN"):
        await provider.complete(req, upstream_model="mock-gpt-4o-mini")


@pytest.mark.asyncio
async def test_mock_provider_fail_mid_stream() -> None:
    """Verifies that MockProvider raises ConnectionResetError mid-stream."""
    provider = MockProvider("test-mock", MockChaosConfig(fail_after_tokens=2))
    req = ChatCompletionRequest(
        model="fast",
        messages=[ChatMessage(role="user", content="A long response is requested here")],
        stream=True,
    )

    yielded_chunks = 0
    with pytest.raises(ConnectionResetError, match="mid-stream connection reset"):
        async for _ in provider.stream(req, upstream_model="mock-gpt-4o-mini"):
            yielded_chunks += 1

    # Should have yielded the initial role chunk plus 2 token chunks
    assert yielded_chunks >= 2
