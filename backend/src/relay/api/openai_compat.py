"""OpenAI-compatible /v1 endpoints with routing, caching, rate limiting, and resilience."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse

from relay.auth import get_current_api_key
from relay.cache import (
    CachedEntry,
    build_cache_key,
    get_response_cache,
    should_cache_request,
)
from relay.circuit_breaker import CircuitState, get_circuit_breaker
from relay.config import Settings, get_settings
from relay.cost import check_budget_available
from relay.db import ApiKeyRecord, RequestLogRecord, get_log_writer
from relay.events import RelayEvent, get_event_bus
from relay.metrics import (
    ACTIVE_STREAMS,
    CACHE_EVENTS,
    COST_TOTAL_USD,
    REQUEST_LATENCY,
    REQUESTS_TOTAL,
    TOKENS_TOTAL,
    TTFT_HISTOGRAM,
)
from relay.providers import (
    ChatCompletionChoice,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    TokenUsage,
    get_provider,
)
from relay.ratelimit import (
    MemoryRateLimiter,
    RateLimiter,
    ResilientRateLimiter,
    SlidingWindowRateLimiter,
    TokenBucketRateLimiter,
)
from relay.retry import is_retryable_error
from relay.router import Router, get_router

logger = logging.getLogger("relay.api.openai")
router = APIRouter(prefix="/v1", tags=["OpenAI Compatible"])

# Concurrency limits per provider target
_provider_semaphores: dict[str, asyncio.Semaphore] = {}
CONCURRENCY_LIMIT_PER_PROVIDER = 50

# Fallback in-memory rate limiter
_memory_limiter = MemoryRateLimiter()

_last_redis_check_ts: float = 0.0
_redis_is_online: bool = False
_shared_redis_client: aioredis.Redis | None = None


def _get_semaphore(provider_name: str) -> asyncio.Semaphore:
    """Returns or creates the concurrency semaphore for a provider."""
    if provider_name not in _provider_semaphores:
        _provider_semaphores[provider_name] = asyncio.Semaphore(CONCURRENCY_LIMIT_PER_PROVIDER)
    return _provider_semaphores[provider_name]


async def _get_active_redis() -> aioredis.Redis | None:
    """Checks and caches whether Redis is currently reachable, refreshing at most every 5 seconds."""
    global _last_redis_check_ts, _redis_is_online, _shared_redis_client
    now = time.time()
    if (now - _last_redis_check_ts) > 5.0:
        _last_redis_check_ts = now
        # Try to ping existing client first
        if _shared_redis_client is not None:
            try:
                await _shared_redis_client.ping()
                _redis_is_online = True
                return _shared_redis_client
            except Exception:
                try:
                    await _shared_redis_client.aclose()
                except Exception:
                    pass
                _shared_redis_client = None
                _redis_is_online = False

        # Create a new client
        settings = get_settings()
        try:
            client = aioredis.from_url(
                settings.redis_url, socket_timeout=0.5, decode_responses=True
            )
            await client.ping()
            _shared_redis_client = client
            _redis_is_online = True
        except Exception:
            _redis_is_online = False
            _shared_redis_client = None
    return _shared_redis_client if _redis_is_online else None


async def _get_rate_limiter() -> RateLimiter:
    """Returns configured rate limiter with resilient in-memory fallback."""
    settings = get_settings()
    r = await _get_active_redis()
    if not r:
        return _memory_limiter

    if settings.relay_rate_limit_algorithm == "sliding_window":
        primary: RateLimiter = SlidingWindowRateLimiter(redis_client=r)
    else:
        primary = TokenBucketRateLimiter(redis_client=r)
    return ResilientRateLimiter(primary=primary, fallback=_memory_limiter)


async def _update_ewma_latency(provider: str, latency_ms: float) -> None:
    """Updates the Exponentially Weighted Moving Average (EWMA) latency in Redis if online."""
    r = await _get_active_redis()
    if not r:
        return
    try:
        key = f"ewma:{provider}:latency"
        old_val = await r.get(key)
        alpha = 0.2
        if old_val:
            new_ewma = (alpha * latency_ms) + ((1.0 - alpha) * float(old_val))
        else:
            new_ewma = latency_ms
        await r.set(key, str(new_ewma), ex=3600)
    except Exception:
        pass


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
    settings: Annotated[Settings, Depends(get_settings)],
) -> Response:
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    event_bus = get_event_bus()
    active_redis = await _get_active_redis()
    cache = get_response_cache(active_redis)

    # 1. Budget Enforcement Check
    is_budget_ok, current_spend = await check_budget_available(
        settings.database_path, api_key.id, api_key.monthly_budget_usd
    )
    if not is_budget_ok:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={
                "error": {
                    "message": f"Monthly budget limit of ${api_key.monthly_budget_usd:.2f} exceeded. Current spend: ${current_spend:.4f}.",
                    "type": "budget_exceeded",
                    "code": 402,
                }
            },
        )

    # 2. Rate Limiting Check (RPM and TPM)
    limiter = await _get_rate_limiter()
    rpm_res = await limiter.check_and_consume(
        key_id=api_key.id,
        limit_type="rpm",
        limit=api_key.rpm_limit,
        cost=1,
    )
    if not rpm_res.allowed:
        headers = {
            "Retry-After": str(rpm_res.retry_after),
            "X-RateLimit-Limit-Requests": str(rpm_res.limit),
            "X-RateLimit-Remaining-Requests": "0",
            "X-RateLimit-Reset-Requests": str(rpm_res.reset_seconds),
        }
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers=headers,
            detail={
                "error": {
                    "message": f"Rate limit exceeded (requests per minute). Please retry after {rpm_res.retry_after} seconds.",
                    "type": "requests",
                    "code": 429,
                }
            },
        )

    est_prompt_tokens = max(
        1, int(sum(len(m.content.split()) for m in request_body.messages) * 1.3)
    )
    tpm_res = await limiter.check_and_consume(
        key_id=api_key.id,
        limit_type="tpm",
        limit=api_key.tpm_limit,
        cost=est_prompt_tokens,
    )
    if not tpm_res.allowed:
        headers = {
            "Retry-After": str(tpm_res.retry_after),
            "X-RateLimit-Limit-Tokens": str(tpm_res.limit),
            "X-RateLimit-Remaining-Tokens": "0",
            "X-RateLimit-Reset-Tokens": str(tpm_res.reset_seconds),
        }
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            headers=headers,
            detail={
                "error": {
                    "message": f"Rate limit exceeded (tokens per minute). Please retry after {tpm_res.retry_after} seconds.",
                    "type": "tokens",
                    "code": 429,
                }
            },
        )

    # 3. Validate Model Alias Permissions
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

    # 4. Check Alias in Router
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

    # 5. Check Cache & Request Coalescing
    cache_control = request.headers.get("Cache-Control")
    can_cache = should_cache_request(request_body, cache_control)
    cache_key = build_cache_key(request_body.model, request_body) if can_cache else ""
    is_leader = True

    if can_cache and cache_key:
        cached = await cache.get(cache_key)
        if cached:
            CACHE_EVENTS.labels(status="hit").inc()
            if not request_body.stream:
                resp_obj = ChatCompletionResponse(
                    id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
                    created=int(time.time()),
                    model=request_body.model,
                    choices=[
                        ChatCompletionChoice(
                            index=0,
                            message=ChatMessage(role="assistant", content=cached.content),
                            finish_reason="stop",
                        )
                    ],
                    usage=TokenUsage(
                        prompt_tokens=cached.prompt_tokens,
                        completion_tokens=cached.completion_tokens,
                        total_tokens=cached.prompt_tokens + cached.completion_tokens,
                    ),
                )
                headers = {
                    "X-Relay-Request-Id": request_id,
                    "X-Relay-Provider": "cache",
                    "X-Relay-Cache": "HIT",
                    "X-Relay-Attempts": "0",
                    "X-Relay-Latency-Ms": "1.50",
                }
                REQUESTS_TOTAL.labels(
                    alias=request_body.model, provider="cache", status="200"
                ).inc()
                return JSONResponse(content=resp_obj.model_dump(), headers=headers)
            else:
                headers = {
                    "X-Relay-Request-Id": request_id,
                    "X-Relay-Provider": "cache",
                    "X-Relay-Cache": "HIT",
                    "X-Relay-Attempts": "0",
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                }
                REQUESTS_TOTAL.labels(
                    alias=request_body.model, provider="cache", status="200"
                ).inc()
                return StreamingResponse(
                    cache.stream_cached_response(cached, request_body.model, request_id),
                    media_type="text/event-stream",
                    headers=headers,
                )

        CACHE_EVENTS.labels(status="miss").inc()
        is_leader, coalesce_event = cache.acquire_single_flight(cache_key)
        if not is_leader:
            try:
                await asyncio.wait_for(coalesce_event.wait(), timeout=15.0)
            except TimeoutError:
                pass
            cached_coalesced = await cache.get(cache_key)
            if cached_coalesced:
                CACHE_EVENTS.labels(status="hit").inc()
                if not request_body.stream:
                    resp_obj = ChatCompletionResponse(
                        id=f"chatcmpl-{uuid.uuid4().hex[:12]}",
                        created=int(time.time()),
                        model=request_body.model,
                        choices=[
                            ChatCompletionChoice(
                                index=0,
                                message=ChatMessage(
                                    role="assistant", content=cached_coalesced.content
                                ),
                                finish_reason="stop",
                            )
                        ],
                        usage=TokenUsage(
                            prompt_tokens=cached_coalesced.prompt_tokens,
                            completion_tokens=cached_coalesced.completion_tokens,
                            total_tokens=cached_coalesced.prompt_tokens
                            + cached_coalesced.completion_tokens,
                        ),
                    )
                    headers = {
                        "X-Relay-Request-Id": request_id,
                        "X-Relay-Provider": "cache",
                        "X-Relay-Cache": "HIT",
                        "X-Relay-Attempts": "0",
                        "X-Relay-Latency-Ms": "2.00",
                    }
                    REQUESTS_TOTAL.labels(
                        alias=request_body.model, provider="cache", status="200"
                    ).inc()
                    return JSONResponse(content=resp_obj.model_dump(), headers=headers)
                else:
                    headers = {
                        "X-Relay-Request-Id": request_id,
                        "X-Relay-Provider": "cache",
                        "X-Relay-Cache": "HIT",
                        "X-Relay-Attempts": "0",
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                    }
                    REQUESTS_TOTAL.labels(
                        alias=request_body.model, provider="cache", status="200"
                    ).inc()
                    return StreamingResponse(
                        cache.stream_cached_response(
                            cached_coalesced, request_body.model, request_id
                        ),
                        media_type="text/event-stream",
                        headers=headers,
                    )

    # 6. Determine Circuit Breaker Status
    open_circuits: set[str] = set()
    for t in alias_cfg.targets:
        cb = get_circuit_breaker(t.provider, redis_client=active_redis)
        if await cb.get_state() == CircuitState.OPEN:
            open_circuits.add(t.provider)

    targets = model_router.plan_targets(request_body.model, open_circuit_providers=open_circuits)
    if not targets:
        if can_cache and cache_key and is_leader:
            cache.release_single_flight(cache_key)
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
    # 7. Non-Streaming Execution
    # ==========================================================================
    if not request_body.stream:
        last_error: Exception | None = None
        attempt_count = 0

        try:
            for target in targets:
                attempt_count += 1
                cb = get_circuit_breaker(target.provider, redis_client=active_redis)
                sem = _get_semaphore(target.provider)

                try:
                    async with sem:
                        provider = get_provider(target.provider)
                        resp = await provider.complete(request_body, target.model)

                    await cb.record_success()
                    total_latency_ms = (time.perf_counter() - start_time) * 1000.0
                    await _update_ewma_latency(target.provider, total_latency_ms)

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

                    cost_usd = (
                        resp.prompt_tokens / 1_000_000.0
                    ) * target.cost_input_per_million + (
                        resp.completion_tokens / 1_000_000.0
                    ) * target.cost_output_per_million

                    # Update Prometheus metrics
                    REQUESTS_TOTAL.labels(
                        alias=request_body.model, provider=target.provider, status="200"
                    ).inc()
                    REQUEST_LATENCY.labels(
                        alias=request_body.model, provider=target.provider
                    ).observe(total_latency_ms / 1000.0)
                    TOKENS_TOTAL.labels(direction="prompt").inc(resp.prompt_tokens)
                    TOKENS_TOTAL.labels(direction="completion").inc(resp.completion_tokens)
                    COST_TOTAL_USD.inc(cost_usd)

                    # Cache result
                    if can_cache and cache_key:
                        entry = CachedEntry(
                            content=resp.content,
                            model=request_body.model,
                            prompt_tokens=resp.prompt_tokens,
                            completion_tokens=resp.completion_tokens,
                            raw_response=response_obj.model_dump(),
                        )
                        await cache.set(cache_key, entry)

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
                    if not is_retryable_error(e):
                        break

            REQUESTS_TOTAL.labels(alias=request_body.model, provider="none", status="502").inc()
            total_latency_ms = (time.perf_counter() - start_time) * 1000.0
            fail_log = RequestLogRecord(
                id=request_id,
                timestamp=datetime.now(UTC).isoformat(),
                api_key_id=api_key.id,
                alias=request_body.model,
                provider="none",
                upstream_model="",
                status=502,
                attempts=attempt_count,
                cache_status="MISS",
                latency_ms=total_latency_ms,
                ttft_ms=None,
                tokens_in=0,
                tokens_out=0,
                cost_usd=0.0,
                error_type=str(last_error)[:200] if last_error else "all_providers_failed",
            )
            get_log_writer().enqueue(fail_log)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "error": {
                        "message": f"All upstream providers failed: {last_error}",
                        "type": "bad_gateway",
                        "code": 502,
                    }
                },
            )
        finally:
            if can_cache and cache_key and is_leader:
                cache.release_single_flight(cache_key)

    # ==========================================================================
    # 8. Streaming Execution
    # ==========================================================================
    async def sse_event_generator() -> AsyncIterator[str]:
        prompt_tokens = max(1, sum(len(m.content.split()) for m in request_body.messages))
        completion_tokens = 0
        ttft_recorded: float | None = None
        attempt_count = 0
        collected_chunks_text: list[str] = []
        ACTIVE_STREAMS.inc()

        try:
            for target in targets:
                attempt_count += 1
                cb = get_circuit_breaker(target.provider, redis_client=active_redis)
                provider = get_provider(target.provider)
                stream_iter = provider.stream(request_body, target.model)

                first_chunk = None
                try:
                    first_chunk = await stream_iter.__anext__()
                except Exception as e:
                    await cb.record_failure(e)
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

                ttft_recorded = (time.perf_counter() - start_time) * 1000.0
                TTFT_HISTOGRAM.labels(alias=request_body.model, provider=target.provider).observe(
                    ttft_recorded / 1000.0
                )
                yield f"data: {first_chunk.model_dump_json()}\n\n"

                try:
                    async for chunk in stream_iter:
                        if chunk.choices and chunk.choices[0].delta.content:
                            collected_chunks_text.append(chunk.choices[0].delta.content)
                        yield f"data: {chunk.model_dump_json()}\n\n"

                    yield "data: [DONE]\n\n"
                    await cb.record_success()

                    total_latency_ms = (time.perf_counter() - start_time) * 1000.0
                    await _update_ewma_latency(target.provider, total_latency_ms)

                    full_content = "".join(collected_chunks_text)
                    completion_tokens = max(1, int(len(full_content.split()) * 1.3))

                    cost_usd = (prompt_tokens / 1_000_000.0) * target.cost_input_per_million + (
                        completion_tokens / 1_000_000.0
                    ) * target.cost_output_per_million

                    REQUESTS_TOTAL.labels(
                        alias=request_body.model, provider=target.provider, status="200"
                    ).inc()
                    REQUEST_LATENCY.labels(
                        alias=request_body.model, provider=target.provider
                    ).observe(total_latency_ms / 1000.0)
                    TOKENS_TOTAL.labels(direction="prompt").inc(prompt_tokens)
                    TOKENS_TOTAL.labels(direction="completion").inc(completion_tokens)
                    COST_TOTAL_USD.inc(cost_usd)

                    if can_cache and cache_key:
                        entry = CachedEntry(
                            content=full_content,
                            model=request_body.model,
                            prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                            raw_response={},
                        )
                        await cache.set(cache_key, entry)

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
                    await cb.record_failure(e)
                    fail_log = RequestLogRecord(
                        id=request_id,
                        timestamp=datetime.now(UTC).isoformat(),
                        api_key_id=api_key.id,
                        alias=request_body.model,
                        provider=target.provider,
                        upstream_model=target.model,
                        status=502,
                        attempts=attempt_count,
                        cache_status="MISS",
                        latency_ms=(time.perf_counter() - start_time) * 1000.0,
                        ttft_ms=ttft_recorded,
                        tokens_in=prompt_tokens,
                        tokens_out=completion_tokens,
                        cost_usd=0.0,
                        error_type=str(e)[:200],
                    )
                    get_log_writer().enqueue(fail_log)
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

            fail_log = RequestLogRecord(
                id=request_id,
                timestamp=datetime.now(UTC).isoformat(),
                api_key_id=api_key.id,
                alias=request_body.model,
                provider="none",
                upstream_model="",
                status=503,
                attempts=attempt_count,
                cache_status="MISS",
                latency_ms=(time.perf_counter() - start_time) * 1000.0,
                ttft_ms=None,
                tokens_in=prompt_tokens,
                tokens_out=0,
                cost_usd=0.0,
                error_type="all_streaming_providers_failed",
            )
            get_log_writer().enqueue(fail_log)
            error_event = {
                "error": {
                    "message": "All candidate providers failed prior to initial token generation.",
                    "type": "service_unavailable",
                    "code": 503,
                }
            }
            yield f"event: error\ndata: {json.dumps(error_event)}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            ACTIVE_STREAMS.dec()
            if can_cache and cache_key and is_leader:
                cache.release_single_flight(cache_key)

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
