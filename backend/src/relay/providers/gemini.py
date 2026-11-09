"""Google Gemini Provider Adapter.

Routes chat completion requests to the Google Generative Language API
(/v1beta/models/{model}:generateContent) and translates Gemini's request /
response schemas and SSE stream events to Relay's OpenAI-compatible types.
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

logger = logging.getLogger("relay.providers.gemini")

DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider(Provider):
    """Adapter for Google Gemini Generative Language REST API."""

    def __init__(
        self,
        api_key: str,
        name: str = "gemini",
        base_url: str = DEFAULT_GEMINI_BASE_URL,
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
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json",
        }

    def _build_payload(self, request: ChatCompletionRequest) -> dict[str, Any]:
        # Collect system messages into systemInstruction
        system_prompts: list[str] = [m.content for m in request.messages if m.role == "system"]
        system_text = "\n\n".join(system_prompts) if system_prompts else None

        contents: list[dict[str, Any]] = []
        for m in request.messages:
            if m.role == "system":
                continue
            gemini_role = "model" if m.role == "assistant" else "user"
            contents.append(
                {
                    "role": gemini_role,
                    "parts": [{"text": m.content}],
                }
            )

        if not contents:
            contents = [{"role": "user", "parts": [{"text": "Hello"}]}]

        generation_config: dict[str, Any] = {
            "temperature": request.temperature,
            "topP": request.top_p,
        }
        if request.max_tokens is not None:
            generation_config["maxOutputTokens"] = request.max_tokens
        if request.stop is not None:
            stop_seqs = [request.stop] if isinstance(request.stop, str) else request.stop
            generation_config["stopSequences"] = stop_seqs

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": generation_config,
        }
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}

        return payload

    def _handle_error(self, exc: Exception) -> RelayProviderError:
        """Normalizes upstream httpx exceptions into RelayProviderError."""
        if isinstance(exc, httpx.TimeoutException):
            return RelayProviderError(
                provider=self.name,
                message=f"Request to Gemini timed out: {exc}",
                status_code=504,
                is_retryable=True,
                raw_error=exc,
            )
        if isinstance(exc, httpx.ConnectError | httpx.NetworkError):
            return RelayProviderError(
                provider=self.name,
                message=f"Network error communicating with Gemini: {exc}",
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
                message=f"Gemini returned error: {error_msg}",
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
        url = f"{self.base_url}/models/{upstream_model}:generateContent"
        headers = self._build_headers()
        payload = self._build_payload(request)

        client = self._get_client(timeout_seconds)
        should_close = client is not self._client

        t0 = time.perf_counter()
        try:
            resp = await client.post(url, headers=headers, json=payload, timeout=timeout_seconds)
            resp.raise_for_status()
            latency_ms = (time.perf_counter() - t0) * 1000.0
            data = resp.json()

            # Parse content from candidates
            candidates = data.get("candidates", [])
            if not candidates and "promptFeedback" in data:
                feedback = data["promptFeedback"]
                block_reason = feedback.get("blockReason", "SAFETY")
                raise RelayProviderError(
                    provider=self.name,
                    message=f"Gemini prompt blocked by safety filters ({block_reason}): {feedback}",
                    status_code=400,
                )

            content = ""
            if candidates:
                cand = candidates[0]
                content_obj = cand.get("content", {})
                parts = content_obj.get("parts", [])
                content = "".join(p.get("text", "") for p in parts)

            usage_metadata = data.get("usageMetadata", {})
            prompt_tokens = usage_metadata.get("promptTokenCount")
            completion_tokens = usage_metadata.get("candidatesTokenCount")

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
        url = f"{self.base_url}/models/{upstream_model}:streamGenerateContent?alt=sse"
        headers = self._build_headers()
        payload = self._build_payload(request)

        client = self._get_client(timeout_seconds)
        should_close = client is not self._client

        msg_id = f"gemini-{int(time.time())}"
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
                        chunk_dict = json.loads(json_str)
                    except json.JSONDecodeError:
                        continue

                    if "promptFeedback" in chunk_dict and not chunk_dict.get("candidates"):
                        feedback = chunk_dict["promptFeedback"]
                        if feedback.get("blockReason"):
                            raise RelayProviderError(
                                provider=self.name,
                                message=f"Gemini stream blocked by safety filter: {feedback}",
                                status_code=400,
                            )

                    candidates = chunk_dict.get("candidates", [])
                    if not candidates:
                        continue

                    cand = candidates[0]
                    content_obj = cand.get("content", {})
                    parts = content_obj.get("parts", [])
                    delta_text = "".join(p.get("text", "") for p in parts)

                    finish_reason_raw = cand.get("finishReason")
                    finish_reason = (
                        "stop" if finish_reason_raw == "STOP" else (finish_reason_raw or None)
                    )

                    usage_meta = chunk_dict.get("usageMetadata", {})
                    if usage_meta:
                        prompt_tokens = usage_meta.get("promptTokenCount", prompt_tokens)
                        completion_tokens = usage_meta.get(
                            "candidatesTokenCount", completion_tokens
                        )

                    usage_obj = None
                    if finish_reason is not None and (prompt_tokens or completion_tokens):
                        usage_obj = TokenUsage(
                            prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                            total_tokens=prompt_tokens + completion_tokens,
                        )

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
                                delta=DeltaMessage(role=role, content=delta_text),
                                finish_reason=finish_reason,
                            )
                        ],
                        usage=usage_obj,
                    )
        except Exception as exc:
            raise self._handle_error(exc) from exc
        finally:
            if should_close:
                await client.aclose()
