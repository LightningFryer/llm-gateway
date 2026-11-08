"""Anthropic Provider Adapter.

Routes chat requests to the Anthropic Messages API (/v1/messages) and translates
Anthropic's message format and SSE stream events to Relay's OpenAI-compatible types.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from relay.cost import estimate_tokens
from relay.providers.base import (
    ChatCompletionChunk,
    ChatCompletionRequest,
    DeltaMessage,
    Provider,
    ProviderResponse,
    RelayProviderError,
    StreamingChoice,
    TokenUsage,
)

logger = logging.getLogger("relay.providers.anthropic")

DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider(Provider):
    """Adapter for Anthropic Claude Messages API."""

    def __init__(
        self,
        api_key: str,
        name: str = "anthropic",
        base_url: str = DEFAULT_ANTHROPIC_BASE_URL,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(name=name)
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._client = client
        self._owns_client = client is None

    def _get_client(self, timeout_seconds: float) -> httpx.AsyncClient:
        if self._client is not None and not self._client.is_closed:
            return self._client
        return httpx.AsyncClient(timeout=timeout_seconds)

    def _build_headers(self) -> dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

    def _build_payload(
        self, request: ChatCompletionRequest, upstream_model: str, stream: bool
    ) -> dict[str, Any]:
        # Anthropic extracts system prompt into top-level 'system' string
        system_prompts: list[str] = [m.content for m in request.messages if m.role == "system"]
        system_content = "\n\n".join(system_prompts) if system_prompts else None

        # Filter messages to user and assistant roles
        messages: list[dict[str, str]] = []
        for m in request.messages:
            if m.role in ("user", "assistant"):
                messages.append({"role": m.role, "content": m.content})

        if not messages:
            messages = [{"role": "user", "content": " "}]

        # Temperature in Anthropic is 0.0 to 1.0 (OpenAI is 0.0 to 2.0)
        anthropic_temp = min(1.0, max(0.0, request.temperature / 2.0))

        payload: dict[str, Any] = {
            "model": upstream_model,
            "messages": messages,
            "max_tokens": request.max_tokens or 4096,
            "temperature": anthropic_temp,
            "stream": stream,
        }
        if system_content:
            payload["system"] = system_content
        if request.top_p < 1.0:
            payload["top_p"] = request.top_p

        return payload

    def _handle_error(self, exc: Exception) -> RelayProviderError:
        """Normalizes upstream httpx exceptions into RelayProviderError."""
        if isinstance(exc, httpx.TimeoutException):
            return RelayProviderError(
                provider=self.name,
                message=f"Request to Anthropic timed out: {exc}",
                status_code=504,
                is_retryable=True,
                raw_error=exc,
            )
        if isinstance(exc, httpx.ConnectError | httpx.NetworkError):
            return RelayProviderError(
                provider=self.name,
                message=f"Network error communicating with Anthropic: {exc}",
                status_code=503,
                is_retryable=True,
                raw_error=exc,
            )
        if isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code
            is_retryable = code in {429, 500, 502, 503, 504, 529}
            error_msg = f"HTTP {code}"
            try:
                body = exc.response.json()
                if "error" in body and isinstance(body["error"], dict):
                    error_msg = body["error"].get("message", error_msg)
            except Exception:
                error_msg = exc.response.text or error_msg

            return RelayProviderError(
                provider=self.name,
                message=f"Anthropic returned error: {error_msg}",
                status_code=code,
                is_retryable=is_retryable,
                raw_error=exc,
            )
        return RelayProviderError(
            provider=self.name,
            message=str(exc),
            status_code=500,
            is_retryable=False,
            raw_error=exc,
        )

    async def complete(
        self,
        request: ChatCompletionRequest,
        upstream_model: str,
        timeout_seconds: float = 30.0,
    ) -> ProviderResponse:
        url = f"{self.base_url}/messages"
        headers = self._build_headers()
        payload = self._build_payload(request, upstream_model, stream=False)

        client = self._get_client(timeout_seconds)
        should_close = client is not self._client

        t0 = time.perf_counter()
        try:
            resp = await client.post(url, headers=headers, json=payload, timeout=timeout_seconds)
            resp.raise_for_status()
            latency_ms = (time.perf_counter() - t0) * 1000.0
            data = resp.json()

            # Anthropic response content is a list of blocks: [{"type": "text", "text": "..."}]
            text_blocks: list[str] = [
                b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
            ]
            content = "".join(text_blocks)

            usage = data.get("usage") or {}
            prompt_tokens = usage.get("input_tokens")
            completion_tokens = usage.get("output_tokens")

            if prompt_tokens is None:
                prompt_tokens = sum(estimate_tokens(m.content) for m in request.messages)
            if completion_tokens is None:
                completion_tokens = estimate_tokens(content)

            return ProviderResponse(
                content=content,
                model=upstream_model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=latency_ms,
                raw_response=data,
            )
        except Exception as exc:
            raise self._handle_error(exc) from exc
        finally:
            if should_close:
                await client.aclose()

    async def stream(
        self,
        request: ChatCompletionRequest,
        upstream_model: str,
        timeout_seconds: float = 30.0,
    ) -> AsyncIterator[ChatCompletionChunk]:
        url = f"{self.base_url}/messages"
        headers = self._build_headers()
        payload = self._build_payload(request, upstream_model, stream=True)

        client = self._get_client(timeout_seconds)
        should_close = client is not self._client

        msg_id = f"msg-{int(time.time())}"
        created_ts = int(time.time())
        is_first = True
        prompt_tokens = 0
        completion_tokens = 0

        try:
            async with client.stream(
                "POST", url, headers=headers, json=payload, timeout=timeout_seconds
            ) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    resp.raise_for_status()

                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data: "):
                        continue

                    json_str = line[len("data: ") :]
                    try:
                        event_data = json.loads(json_str)
                    except json.JSONDecodeError:
                        continue

                    event_type = event_data.get("type")

                    if event_type == "message_start":
                        msg_info = event_data.get("message", {})
                        msg_id = msg_info.get("id", msg_id)
                        usage = msg_info.get("usage", {})
                        prompt_tokens = usage.get("input_tokens", 0)

                    elif event_type == "content_block_delta":
                        delta_dict = event_data.get("delta", {})
                        delta_type = delta_dict.get("type")
                        if delta_type == "text_delta":
                            chunk_text = delta_dict.get("text", "")
                            role = "assistant" if is_first else None
                            is_first = False

                            yield ChatCompletionChunk(
                                id=msg_id,
                                object="chat.completion.chunk",
                                created=created_ts,
                                model=upstream_model,
                                choices=[
                                    StreamingChoice(
                                        index=0,
                                        delta=DeltaMessage(role=role, content=chunk_text),
                                        finish_reason=None,
                                    )
                                ],
                            )

                    elif event_type == "message_delta":
                        usage = event_data.get("usage", {})
                        completion_tokens = usage.get("output_tokens", 0)
                        delta_info = event_data.get("delta", {})
                        stop_reason = delta_info.get("stop_reason")
                        # Translate stop reason
                        finish_reason = (
                            "stop" if stop_reason == "end_turn" else (stop_reason or "stop")
                        )

                        yield ChatCompletionChunk(
                            id=msg_id,
                            object="chat.completion.chunk",
                            created=created_ts,
                            model=upstream_model,
                            choices=[
                                StreamingChoice(
                                    index=0,
                                    delta=DeltaMessage(),
                                    finish_reason=finish_reason,
                                )
                            ],
                            usage=TokenUsage(
                                prompt_tokens=prompt_tokens,
                                completion_tokens=completion_tokens,
                                total_tokens=prompt_tokens + completion_tokens,
                            ),
                        )

                    elif event_type == "message_stop":
                        break
        except Exception as exc:
            raise self._handle_error(exc) from exc
        finally:
            if should_close:
                await client.aclose()
