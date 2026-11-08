"""OpenAI-compatible /v1 endpoints: /v1/chat/completions and /v1/models."""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse

from relay.auth import get_current_api_key
from relay.db import ApiKeyRecord, RequestLogRecord, get_log_writer
from relay.providers import (
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    TokenUsage,
    get_provider,
)
from relay.router import Router, get_router

logger = logging.getLogger("relay.api.openai")
router = APIRouter(prefix="/v1", tags=["OpenAI Compatible"])


@router.get("/models", summary="List Available Model Aliases")
async def list_models(
    _api_key: Annotated[ApiKeyRecord, Depends(get_current_api_key)],
    model_router: Annotated[Router, Depends(get_router)],
) -> dict[str, object]:
    """Returns the list of configured model aliases in OpenAI model format."""
    return {
        "object": "list",
        "data": model_router.list_models(),
    }


@router.post("/chat/completions", summary="Create Chat Completion")
async def create_chat_completion(
    request_body: ChatCompletionRequest,
    request: Request,
    api_key: Annotated[ApiKeyRecord, Depends(get_current_api_key)],
    model_router: Annotated[Router, Depends(get_router)],
) -> Response:
    """Handles both non-streaming and streaming chat completions.

    Routes request across configured targets and returns OpenAI-compatible responses.
    """
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))

    # 1. Validate Model Alias Permissions
    allowed = api_key.allowed_aliases
    if "*" not in allowed and request_body.model not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "message": f"API key does not have permission to access model '{request_body.model}'.",
                    "type": "permission_denied",
                    "code": 403,
                }
            },
        )

    # 2. Check Alias in Router
    alias_cfg = model_router.get_alias(request_body.model)
    if not alias_cfg:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "message": f"The model alias '{request_body.model}' does not exist.",
                    "type": "invalid_request_error",
                    "param": "model",
                    "code": "model_not_found",
                }
            },
        )

    # 3. Select Target Candidate
    targets = model_router.plan_targets(request_body.model)
    if not targets:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            headers={"Retry-After": "10"},
            detail={
                "error": {
                    "message": "All provider targets for this model are currently unavailable (circuits open).",
                    "type": "service_unavailable",
                    "code": 503,
                }
            },
        )

    target = targets[0]
    provider = get_provider(target.provider)
    start_time = time.perf_counter()

    # 4. Handle Non-Streaming Completion
    if not request_body.stream:
        try:
            resp = await provider.complete(request_body, target.model)
            total_latency_ms = (time.perf_counter() - start_time) * 1000.0

            completion_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
            created_ts = int(time.time())

            response_obj = ChatCompletionResponse(
                id=completion_id,
                created=created_ts,
                model=request_body.model,
                choices=[
                    ChatCompletionChoice(
                        index=0,
                        message=ChatMessage(role="assistant", content=resp.content),
                        finish_reason="stop",
                    )
                ],
                usage=TokenUsage(
                    prompt_tokens=resp.prompt_tokens,
                    completion_tokens=resp.completion_tokens,
                    total_tokens=resp.prompt_tokens + resp.completion_tokens,
                ),
            )

            # Compute approximate cost
            cost_usd = (resp.prompt_tokens / 1_000_000.0) * target.cost_input_per_million + (
                resp.completion_tokens / 1_000_000.0
            ) * target.cost_output_per_million

            # Enqueue audit log asynchronously off the hot path
            log_record = RequestLogRecord(
                id=request_id,
                timestamp=datetime.now(UTC).isoformat(),
                api_key_id=api_key.id,
                alias=request_body.model,
                provider=target.provider,
                upstream_model=target.model,
                status=200,
                attempts=1,
                cache_status="MISS",
                latency_ms=total_latency_ms,
                ttft_ms=resp.ttft_ms,
                tokens_in=resp.prompt_tokens,
                tokens_out=resp.completion_tokens,
                cost_usd=cost_usd,
                error_type=None,
            )
            get_log_writer().enqueue(log_record)

            headers = {
                "X-Relay-Request-Id": request_id,
                "X-Relay-Provider": target.provider,
                "X-Relay-Cache": "MISS",
                "X-Relay-Attempts": "1",
                "X-Relay-Latency-Ms": f"{total_latency_ms:.2f}",
            }
            return JSONResponse(content=response_obj.model_dump(), headers=headers)

        except Exception as e:
            total_latency_ms = (time.perf_counter() - start_time) * 1000.0
            logger.error("Provider execution failed: %s", e)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={"error": {"message": str(e), "type": "bad_gateway", "code": 502}},
            ) from e

    # 5. Handle Streaming Completion
    async def sse_event_generator() -> AsyncIterator[str]:
        prompt_tokens = max(1, sum(len(m.content.split()) for m in request_body.messages))
        completion_tokens = 0
        ttft_recorded: float | None = None

        try:
            async for chunk in provider.stream(request_body, target.model):
                if ttft_recorded is None:
                    ttft_recorded = (time.perf_counter() - start_time) * 1000.0

                if chunk.choices and chunk.choices[0].delta.content:
                    completion_tokens += 1

                # Format as Server-Sent Event
                yield f"data: {chunk.model_dump_json()}\n\n"

            # Terminal OpenAI streaming chunk
            yield "data: [DONE]\n\n"

            total_latency_ms = (time.perf_counter() - start_time) * 1000.0
            cost_usd = (prompt_tokens / 1_000_000.0) * target.cost_input_per_million + (
                completion_tokens / 1_000_000.0
            ) * target.cost_output_per_million

            # Asynchronously log stream completion
            log_record = RequestLogRecord(
                id=request_id,
                timestamp=datetime.now(UTC).isoformat(),
                api_key_id=api_key.id,
                alias=request_body.model,
                provider=target.provider,
                upstream_model=target.model,
                status=200,
                attempts=1,
                cache_status="MISS",
                latency_ms=total_latency_ms,
                ttft_ms=ttft_recorded,
                tokens_in=prompt_tokens,
                tokens_out=completion_tokens,
                cost_usd=cost_usd,
                error_type=None,
            )
            get_log_writer().enqueue(log_record)

        except Exception as e:
            logger.error("Streaming error: %s", e)
            error_event = {
                "error": {
                    "message": f"Stream error: {e}",
                    "type": "upstream_error",
                    "code": 502,
                }
            }
            yield f"event: error\ndata: {json.dumps(error_event)}\n\n"
            yield "data: [DONE]\n\n"

    response_headers = {
        "X-Relay-Request-Id": request_id,
        "X-Relay-Provider": target.provider,
        "X-Relay-Cache": "MISS",
        "X-Relay-Attempts": "1",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
    }
    return StreamingResponse(
        sse_event_generator(),
        media_type="text/event-stream",
        headers=response_headers,
    )
