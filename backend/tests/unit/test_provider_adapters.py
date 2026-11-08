"""Unit tests for OpenAI, Anthropic, and Gemini provider adapters using respx."""

from __future__ import annotations

import pytest
import respx

from relay.providers import (
    AnthropicProvider,
    ChatCompletionRequest,
    ChatMessage,
    GeminiProvider,
    MockProvider,
    OpenAIProvider,
    RelayProviderError,
    get_provider,
)


@pytest.fixture
def sample_request() -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="test-model",
        messages=[
            ChatMessage(role="system", content="You are a helpful assistant."),
            ChatMessage(role="user", content="Hello, world!"),
        ],
        temperature=0.7,
        max_tokens=100,
    )


# -----------------------------------------------------------------------------
# OpenAI Adapter Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_openai_complete_success(sample_request: ChatCompletionRequest) -> None:
    respx.post("https://api.openai.com/v1/chat/completions").respond(
        status_code=200,
        json={
            "id": "chatcmpl-test-123",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "Hello from mocked OpenAI!"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 15, "completion_tokens": 6, "total_tokens": 21},
        },
    )

    provider = OpenAIProvider(api_key="sk-test-key")
    resp = await provider.complete(sample_request, upstream_model="gpt-4o-mini")

    assert resp.content == "Hello from mocked OpenAI!"
    assert resp.model == "gpt-4o-mini"
    assert resp.prompt_tokens == 15
    assert resp.completion_tokens == 6
    assert resp.latency_ms >= 0


@pytest.mark.asyncio
@respx.mock
async def test_openai_stream_success(sample_request: ChatCompletionRequest) -> None:
    sse_body = (
        'data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{"role":"assistant","content":"Hello"}}]}\n\n'
        'data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{"content":" streaming"}}]}\n\n'
        'data: {"id":"chatcmpl-1","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
        "data: [DONE]\n\n"
    )

    respx.post("https://api.openai.com/v1/chat/completions").respond(
        status_code=200,
        content=sse_body,
        headers={"content-type": "text/event-stream"},
    )

    provider = OpenAIProvider(api_key="sk-test-key")
    collected = []
    async for chunk in provider.stream(sample_request, upstream_model="gpt-4o-mini"):
        if chunk.choices and chunk.choices[0].delta.content:
            collected.append(chunk.choices[0].delta.content)

    assert "".join(collected) == "Hello streaming"


@pytest.mark.asyncio
@respx.mock
async def test_openai_error_handling(sample_request: ChatCompletionRequest) -> None:
    # 429 Rate Limit error is retryable
    respx.post("https://api.openai.com/v1/chat/completions").respond(
        status_code=429,
        json={"error": {"message": "Rate limit exceeded"}},
    )

    provider = OpenAIProvider(api_key="sk-test-key")
    with pytest.raises(RelayProviderError) as exc_info:
        await provider.complete(sample_request, upstream_model="gpt-4o-mini")

    assert exc_info.value.status_code == 429
    assert exc_info.value.is_retryable is True
    assert "Rate limit exceeded" in exc_info.value.message


# -----------------------------------------------------------------------------
# Anthropic Adapter Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_complete_success(sample_request: ChatCompletionRequest) -> None:
    respx.post("https://api.anthropic.com/v1/messages").respond(
        status_code=200,
        json={
            "id": "msg_01mocked",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "Hello from mocked Claude!"}],
            "model": "claude-3-5-sonnet-20241022",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 20, "output_tokens": 8},
        },
    )

    provider = AnthropicProvider(api_key="sk-ant-test")
    resp = await provider.complete(sample_request, upstream_model="claude-3-5-sonnet")

    assert resp.content == "Hello from mocked Claude!"
    assert resp.prompt_tokens == 20
    assert resp.completion_tokens == 8


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_stream_success(sample_request: ChatCompletionRequest) -> None:
    sse_body = (
        'data: {"type": "message_start", "message": {"id": "msg_123", "usage": {"input_tokens": 18}}}\n\n'
        'data: {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Claude"}}\n\n'
        'data: {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": " rocks!"}}\n\n'
        'data: {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 5}}\n\n'
        'data: {"type": "message_stop"}\n\n'
    )

    respx.post("https://api.anthropic.com/v1/messages").respond(
        status_code=200,
        content=sse_body,
        headers={"content-type": "text/event-stream"},
    )

    provider = AnthropicProvider(api_key="sk-ant-test")
    collected = []
    finish_reason = None
    async for chunk in provider.stream(sample_request, upstream_model="claude-3-5-sonnet"):
        if chunk.choices and chunk.choices[0].delta.content:
            collected.append(chunk.choices[0].delta.content)
        if chunk.choices and chunk.choices[0].finish_reason:
            finish_reason = chunk.choices[0].finish_reason

    assert "".join(collected) == "Claude rocks!"
    assert finish_reason == "stop"


@pytest.mark.asyncio
@respx.mock
async def test_anthropic_error_handling(sample_request: ChatCompletionRequest) -> None:
    # 529 Overloaded is retryable
    respx.post("https://api.anthropic.com/v1/messages").respond(
        status_code=529,
        json={"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
    )

    provider = AnthropicProvider(api_key="sk-ant-test")
    with pytest.raises(RelayProviderError) as exc_info:
        await provider.complete(sample_request, upstream_model="claude-3-5-sonnet")

    assert exc_info.value.status_code == 529
    assert exc_info.value.is_retryable is True


# -----------------------------------------------------------------------------
# Gemini Adapter Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@respx.mock
async def test_gemini_complete_success(sample_request: ChatCompletionRequest) -> None:
    respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent"
    ).respond(
        status_code=200,
        json={
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": "Hello from Gemini 1.5!"}],
                        "role": "model",
                    },
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 12,
                "candidatesTokenCount": 7,
                "totalTokenCount": 19,
            },
        },
    )

    provider = GeminiProvider(api_key="gemini-key-123")
    resp = await provider.complete(sample_request, upstream_model="gemini-1.5-pro")

    assert resp.content == "Hello from Gemini 1.5!"
    assert resp.prompt_tokens == 12
    assert resp.completion_tokens == 7


@pytest.mark.asyncio
@respx.mock
async def test_gemini_stream_success(sample_request: ChatCompletionRequest) -> None:
    sse_body = (
        'data: {"candidates": [{"content": {"parts": [{"text": "Gemini"}]}}]}\n\n'
        'data: {"candidates": [{"content": {"parts": [{"text": " stream"}]}, "finishReason": "STOP"}], "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 4}}\n\n'
    )

    respx.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:streamGenerateContent?alt=sse"
    ).respond(
        status_code=200,
        content=sse_body,
        headers={"content-type": "text/event-stream"},
    )

    provider = GeminiProvider(api_key="gemini-key-123")
    collected = []
    finish_reason = None
    async for chunk in provider.stream(sample_request, upstream_model="gemini-1.5-pro"):
        if chunk.choices and chunk.choices[0].delta.content:
            collected.append(chunk.choices[0].delta.content)
        if chunk.choices and chunk.choices[0].finish_reason:
            finish_reason = chunk.choices[0].finish_reason

    assert "".join(collected) == "Gemini stream"
    assert finish_reason == "stop"


@pytest.mark.asyncio
async def test_offline_fallback_when_no_keys_configured(
    sample_request: ChatCompletionRequest,
) -> None:
    # Any unregistered provider name falls back cleanly to MockProvider
    provider = get_provider("unconfigured-provider")
    assert isinstance(provider, MockProvider)
    resp = await provider.complete(sample_request, upstream_model="fallback-model")
    assert resp.content
    assert resp.prompt_tokens > 0
