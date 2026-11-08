"""OpenAI Provider Adapter.

Routes chat completion requests to the official OpenAI REST API and translates
responses and SSE streaming chunks into Relay's internal schemas.
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

logger = logging.getLogger("relay.providers.openai")

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"


class OpenAIProvider(Provider):
    """Adapter for official OpenAI API endpoints."""

    def __init__(
        self,
        api_key: str,
        name: str = "openai",
        base_url: str = DEFAULT_OPENAI_BASE_URL,
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
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _build_payload(
        self, request: ChatCompletionRequest, upstream_model: str, stream: bool
    ) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        for m in request.messages:
            msg_dict: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.name:
                msg_dict["name"] = m.name
            messages.append(msg_dict)

        payload: dict[str, Any] = {
            "model": upstream_model,
            "messages": messages,
            "temperature": request.temperature,
            "top_p": request.top_p,
            "stream": stream,
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.stop is not None:
            payload["stop"] = request.stop
        if request.user is not None:
            payload["user"] = request.user
        return payload

    def _handle_error(self, exc: Exception) -> RelayProviderError:
        """Normalizes upstream httpx exceptions into RelayProviderError."""
        if isinstance(exc, httpx.TimeoutException):
            return RelayProviderError(
                provider=self.name,
                message=f"Request to OpenAI timed out: {exc}",
                status_code=504,
                is_retryable=True,
                raw_error=exc,
            )
        if isinstance(exc, httpx.ConnectError | httpx.NetworkError):
            return RelayProviderError(
                provider=self.name,
                message=f"Network error communicating with OpenAI: {exc}",
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
                message=f"OpenAI returned error: {error_msg}",
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
        url = f"{self.base_url}/chat/completions"
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

            choice = data["choices"][0]
            content = choice["message"].get("content", "")

            usage = data.get("usage") or {}
            prompt_tokens = usage.get("prompt_tokens")
            completion_tokens = usage.get("completion_tokens")

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
        url = f"{self.base_url}/chat/completions"
        headers = self._build_headers()
        payload = self._build_payload(request, upstream_model, stream=True)

        client = self._get_client(timeout_seconds)
        should_close = client is not self._client

        try:
            async with client.stream(
                "POST", url, headers=headers, json=payload, timeout=timeout_seconds
            ) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    resp.raise_for_status()

                async for line in resp.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    if line == "data: [DONE]":
                        break
                    if line.startswith("data: "):
                        json_str = line[len("data: ") :]
                        try:
                            chunk_dict = json.loads(json_str)
                            # Convert choices dict to StreamingChoice models
                            choices: list[StreamingChoice] = []
                            for c in chunk_dict.get("choices", []):
                                delta_dict = c.get("delta", {})
                                choices.append(
                                    StreamingChoice(
                                        index=c.get("index", 0),
                                        delta=DeltaMessage(
                                            role=delta_dict.get("role"),
                                            content=delta_dict.get("content"),
                                        ),
                                        finish_reason=c.get("finish_reason"),
                                    )
                                )
                            usage_obj = None
                            if "usage" in chunk_dict and chunk_dict["usage"] is not None:
                                usage_obj = TokenUsage(**chunk_dict["usage"])

                            yield ChatCompletionChunk(
                                id=chunk_dict.get("id", f"chatcmpl-{int(time.time())}"),
                                object="chat.completion.chunk",
                                created=chunk_dict.get("created", int(time.time())),
                                model=upstream_model,
                                choices=choices,
                                usage=usage_obj,
                            )
                        except json.JSONDecodeError:
                            continue
        except Exception as exc:
            raise self._handle_error(exc) from exc
        finally:
            if should_close:
                await client.aclose()
