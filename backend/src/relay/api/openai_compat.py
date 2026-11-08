"""OpenAI-compatible /v1 endpoints with transparent failover and resilience engine."""

from __future__ import annotations

import asyncio
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
from relay.circuit_breaker import CircuitState, get_circuit_breaker
from relay.db import ApiKeyRecord, RequestLogRecord, get_log_writer
from relay.events import RelayEvent, get_event_bus
from relay.providers import (
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    TokenUsage,
    get_provider,
)
from relay.retry import is_retryable_error
from relay.router import Router, get_router

logger = logging.getLogger("relay.api.openai")
router = APIRouter(prefix="/v1", tags=["OpenAI Compatible"])

# Concurrency limits per provider target
_provider_semaphores: dict[str, asyncio.Semaphore] = {}
CONCURRENCY_LIMIT_PER_PROVIDER = 50


def _get_semaphore(provider_name: str) -> asyncio.Semaphore:
    """Returns or creates the concurrency semaphore for a provider."""
    if provider_name not in _provider_semaphores:
        _provider_semaphores[provider_name] = asyncio.Semaphore(CONCURRENCY_LIMIT_PER_PROVIDER)
    return _provider_semaphores[provider_name]


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
    """Handles chat completions with automatic failover, retries, and circuit breaking."""
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    event_bus = get_event_bus()

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

    # 3. Determine Circuit Breaker Status of all targets
    open_circuits: set[str] = set()
    for t in alias_cfg.targets:
        cb = get_circuit_breaker(t.provider)
        if await cb.get_state() == CircuitState.OPEN:
            open_circuits.add(t.provider)

    # 4. Plan Candidates based on routing strategy (excluding OPEN circuits)
    targets = model_router.plan_targets(request_body.model, open_circuit_providers=open_circuits)
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

    start_time = time.perf_counter()

    # ==========================================================================
    # 5. Non-Streaming Execution with Transparent Failover
    # ==========================================================================
    if not request_body.stream:
        last_error: Exception | None = None
        attempt_count = 0

        for target in targets:
            attempt_count += 1
            cb = get_circuit_breaker(target.provider)
            sem = _get_semaphore(target.provider)

            # Enforce provider concurrency limit
            if sem.locked():
                # If saturated, move to next target
                logger.warning(
                    "Provider '%s' semaphore saturated, attempting failover", target.provider
                )
                continue

            try:
                async with sem:
                    provider = get_provider(target.provider)
                    resp = await provider.complete(request_body, target.model)

                # Record success on circuit breaker
                await cb.record_success()

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

                cost_usd = (resp.prompt_tokens / 1_000_000.0) * target.cost_input_per_million + (
                    resp.completion_tokens / 1_000_000.0
                ) * target.cost_output_per_million

                # Asynchronously queue log record
                log_record = RequestLogRecord(
                    id=request_id,
                    timestamp=datetime.now(UTC).isoformat(),
                    api_key_id=api_key.id,
                    alias=request_body.model,
                    provider=target.provider,
                    upstream_model=target.model,
                    status=200,
                    attempts=attempt_count,
                    cache_status="MISS",
                    latency_ms=total_latency_ms,
                    ttft_ms=resp.ttft_ms,
                    tokens_in=resp.prompt_tokens,
                    tokens_out=resp.completion_tokens,
                    cost_usd=cost_usd,
                    error_type=None,
                )
                get_log_writer().enqueue(log_record)

                # Emit completion event
                await event_bus.publish(
                    RelayEvent(
                        event_type="request.completed",
                        data={
                            "id": request_id,
                            "alias": request_body.model,
                            "provider": target.provider,
                            "latency_ms": round(total_latency_ms, 2),
                            "attempts": attempt_count,
                            "cost_usd": cost_usd,
                            "cache_status": "MISS",
                            "status": 200,
                        },
                    )
                )

                headers = {
                    "X-Relay-Request-Id": request_id,
                    "X-Relay-Provider": target.provider,
                    "X-Relay-Cache": "MISS",
                    "X-Relay-Attempts": str(attempt_count),
                    "X-Relay-Latency-Ms": f"{total_latency_ms:.2f}",
                }
                return JSONResponse(content=response_obj.model_dump(), headers=headers)

            except Exception as e:
                last_error = e
                await cb.record_failure(e)
                logger.warning(
                    "Target provider '%s' failed on attempt %d: %s. Initiating failover.",
                    target.provider,
                    attempt_count,
                    e,
                )
                # Emit failover event
                await event_bus.publish(
                    RelayEvent(
                        event_type="failover",
                        data={
                            "id": request_id,
                            "failed_provider": target.provider,
                            "error": str(e),
                            "attempt": attempt_count,
                        },
                    )
                )
                # If error is not retryable, do not continue trying
                if not is_retryable_error(e):
                    break

        # All targets failed
        total_latency_ms = (time.perf_counter() - start_time) * 1000.0
        logger.error(
            "All candidate targets exhausted for alias '%s'. Last error: %s",
            request_body.model,
            last_error,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": {
                    "message": f"All upstream providers failed. Last error: {last_error}",
                    "type": "bad_gateway",
                    "code": 502,
                }
            },
        )

    # ==========================================================================
    # 6. Streaming Execution with Transparent Pre-Token Failover
    # ==========================================================================
    async def sse_event_generator() -> AsyncIterator[str]:
        prompt_tokens = max(1, sum(len(m.content.split()) for m in request_body.messages))
        completion_tokens = 0
        ttft_recorded: float | None = None
        attempt_count = 0

        # Iterate through target candidates until we successfully connect and yield the first token
        for target in targets:
            attempt_count += 1
            cb = get_circuit_breaker(target.provider)
            provider = get_provider(target.provider)
            stream_iter = provider.stream(request_body, target.model)

            first_chunk = None
            try:
                # Pre-first-token phase: attempt to receive the initial chunk
                first_chunk = await stream_iter.__anext__()
            except (
                ConnectionError,
                ConnectionResetError,
                RuntimeError,
                TimeoutError,
                Exception,
            ) as e:
                # Failed before first token: transparently fall over to next target!
                await cb.record_failure(e)
                logger.warning(
                    "Streaming pre-token failure on '%s' (attempt %d): %s. Failing over.",
                    target.provider,
                    attempt_count,
                    e,
                )
                await event_bus.publish(
                    RelayEvent(
                        event_type="failover",
                        data={
                            "id": request_id,
                            "failed_provider": target.provider,
                            "error": str(e),
                            "attempt": attempt_count,
                        },
                    )
                )
                continue

            # We successfully received the first chunk!
            ttft_recorded = (time.perf_counter() - start_time) * 1000.0
            yield f"data: {first_chunk.model_dump_json()}\n\n"

            # Mid-stream phase: yield subsequent chunks
            try:
                async for chunk in stream_iter:
                    if chunk.choices and chunk.choices[0].delta.content:
                        completion_tokens += 1
                    yield f"data: {chunk.model_dump_json()}\n\n"

                # Stream completed successfully
                yield "data: [DONE]\n\n"
                await cb.record_success()

                total_latency_ms = (time.perf_counter() - start_time) * 1000.0
                cost_usd = (prompt_tokens / 1_000_000.0) * target.cost_input_per_million + (
                    completion_tokens / 1_000_000.0
                ) * target.cost_output_per_million

                log_record = RequestLogRecord(
                    id=request_id,
                    timestamp=datetime.now(UTC).isoformat(),
                    api_key_id=api_key.id,
                    alias=request_body.model,
                    provider=target.provider,
                    upstream_model=target.model,
                    status=200,
                    attempts=attempt_count,
                    cache_status="MISS",
                    latency_ms=total_latency_ms,
                    ttft_ms=ttft_recorded,
                    tokens_in=prompt_tokens,
                    tokens_out=completion_tokens,
                    cost_usd=cost_usd,
                    error_type=None,
                )
                get_log_writer().enqueue(log_record)

                await event_bus.publish(
                    RelayEvent(
                        event_type="request.completed",
                        data={
                            "id": request_id,
                            "alias": request_body.model,
                            "provider": target.provider,
                            "latency_ms": round(total_latency_ms, 2),
                            "attempts": attempt_count,
                            "cost_usd": cost_usd,
                            "cache_status": "MISS",
                            "status": 200,
                        },
                    )
                )
                return

            except Exception as e:
                # Mid-stream failure: cannot transparently switch providers.
                # Must emit terminal SSE error event, record failure, and end stream.
                logger.error("Mid-stream failure on provider '%s': %s", target.provider, e)
                await cb.record_failure(e)
                error_event = {
                    "error": {
                        "message": f"Upstream provider connection terminated mid-stream: {e}",
                        "type": "upstream_error",
                        "code": 502,
                    }
                }
                yield f"event: error\ndata: {json.dumps(error_event)}\n\n"
                yield "data: [DONE]\n\n"
                return

        # If all candidates failed before yielding any token
        error_event = {
            "error": {
                "message": "All candidate providers failed prior to initial token generation.",
                "type": "service_unavailable",
                "code": 503,
            }
        }
        yield f"event: error\ndata: {json.dumps(error_event)}\n\n"
        yield "data: [DONE]\n\n"

    response_headers = {
        "X-Relay-Request-Id": request_id,
        "X-Relay-Provider": targets[0].provider,
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
