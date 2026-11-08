"""Admin API endpoints for system monitoring, chaos injection, key management, and SSE events."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Annotated, Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from relay.auth import generate_api_key, verify_admin_key
from relay.circuit_breaker import CircuitState, get_all_circuit_breakers, get_circuit_breaker
from relay.config import get_settings
from relay.db import (
    create_api_key,
    get_api_key_by_id,
    get_monthly_spend,
    get_recent_logs,
    get_stats_summary,
    get_usage_by_provider,
    list_api_keys,
    rotate_api_key,
    update_api_key,
)
from relay.events import RelayEvent, get_event_bus
from relay.providers.mock import MockProvider, get_all_providers, get_provider
from relay.router import Router, get_router

logger = logging.getLogger("relay.api.admin")
router = APIRouter(prefix="/admin", tags=["Admin API"])


# Schemas
class ChaosUpdateRequest(BaseModel):
    provider: str
    is_down: bool | None = None
    base_latency_ms: float | None = None
    jitter_ms: float | None = None
    error_rate: float | None = None
    slow_ttft_ms: float | None = None
    fail_after_tokens: int | None = None


class CreateKeyRequest(BaseModel):
    name: str
    rpm_limit: int = 60
    tpm_limit: int = 60000
    monthly_budget_usd: float = 50.0
    allowed_aliases: list[str] = Field(default_factory=lambda: ["*"])


class UpdateKeyRequest(BaseModel):
    enabled: bool | None = None
    rpm_limit: int | None = None
    tpm_limit: int | None = None
    monthly_budget_usd: float | None = None
    allowed_aliases: list[str] | None = None


# ==============================================================================
# Providers & Chaos Controls
# ==============================================================================


@router.get("/providers", summary="List Providers with Health & Circuit State")
async def list_providers(
    _admin: Annotated[str, Depends(verify_admin_key)],
) -> list[dict[str, Any]]:
    """Returns runtime status, circuit breaker states, and moving-average latencies for all providers."""
    providers = get_all_providers()
    settings = get_settings()
    results: list[dict[str, Any]] = []

    # Read Redis EWMA latencies if online
    latencies: dict[str, float] = {}
    try:
        r = aioredis.from_url(settings.redis_url, socket_timeout=0.1, decode_responses=True)
        for name in providers:
            val = await r.get(f"ewma:{name}:latency")
            if val:
                latencies[name] = float(val)
        await r.aclose()
    except Exception:
        pass

    for name, p in providers.items():
        cb = get_circuit_breaker(name)
        cb_state = await cb.get_state()
        if isinstance(p, MockProvider):
            is_mock = True
            chaos_info = p.chaos.__dict__
            is_down = p.chaos.is_down
        else:
            is_mock = False
            chaos_info = {}
            is_down = False

        results.append(
            {
                "name": name,
                "circuit_state": cb_state.value,
                "is_healthy": cb_state != CircuitState.OPEN and not is_down,
                "ewma_latency_ms": round(latencies.get(name, 50.0), 2),
                "is_mock": is_mock,
                "chaos": chaos_info,
            }
        )
    return results


@router.post("/chaos", summary="Update Mock Provider Chaos Knobs")
async def update_chaos(
    req: ChaosUpdateRequest,
    _admin: Annotated[str, Depends(verify_admin_key)],
) -> dict[str, Any]:
    """Injects latency, forced downtime, error rates, or mid-stream drops into a mock provider."""
    provider = get_provider(req.provider)
    if not isinstance(provider, MockProvider):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider '{req.provider}' is not a controllable MockProvider.",
        )

    provider.update_chaos(
        is_down=req.is_down,
        base_latency_ms=req.base_latency_ms,
        jitter_ms=req.jitter_ms,
        error_rate=req.error_rate,
        slow_ttft_ms=req.slow_ttft_ms,
        fail_after_tokens=req.fail_after_tokens,
    )

    cb = get_circuit_breaker(req.provider)
    if req.is_down is True:
        # If presenter manually kills provider, also trip circuit OPEN to demonstrate fast failover
        await cb.force_state(CircuitState.OPEN)
    elif req.is_down is False and await cb.get_state() == CircuitState.OPEN:
        # When restored, move to HALF_OPEN to demonstrate probe recovery
        await cb.force_state(CircuitState.HALF_OPEN)

    # Publish chaos.changed event to EventBus for live dashboard
    await get_event_bus().publish(
        RelayEvent(
            event_type="chaos.changed",
            data={"provider": req.provider, "chaos": provider.chaos.__dict__},
        )
    )

    return {"status": "ok", "provider": req.provider, "chaos": provider.chaos.__dict__}


@router.post("/chaos/reset", summary="Reset All Chaos Knobs to Defaults")
async def reset_all_chaos(
    _admin: Annotated[str, Depends(verify_admin_key)],
) -> dict[str, str]:
    """Resets all mock providers and circuit breakers back to normal healthy states."""
    providers = get_all_providers()
    cbs = get_all_circuit_breakers()

    for p in providers.values():
        if isinstance(p, MockProvider):
            p.reset_chaos()

    for cb in cbs.values():
        await cb.force_state(CircuitState.CLOSED)

    await get_event_bus().publish(
        RelayEvent(event_type="chaos.changed", data={"action": "reset_all"})
    )
    return {"status": "ok", "message": "All chaos controls and circuit breakers reset to healthy."}


# ==============================================================================
# API Key Management
# ==============================================================================


@router.get("/keys", summary="List All API Keys with Usage")
async def list_keys(
    _admin: Annotated[str, Depends(verify_admin_key)],
    settings: Annotated[Any, Depends(get_settings)],
) -> list[dict[str, Any]]:
    """Lists all configured API keys with their rate limits, budgets, and month-to-date spend."""
    keys = await list_api_keys(settings.database_path)
    results = []
    for k in keys:
        spend = await get_monthly_spend(settings.database_path, k.id)
        d = k.to_dict()
        d["spend_this_month_usd"] = round(spend, 4)
        d["budget_remaining_usd"] = round(max(0.0, k.monthly_budget_usd - spend), 4)
        results.append(d)
    return results


@router.post("/keys", summary="Create New API Key")
async def create_key(
    req: CreateKeyRequest,
    _admin: Annotated[str, Depends(verify_admin_key)],
    settings: Annotated[Any, Depends(get_settings)],
) -> dict[str, Any]:
    """Creates a new API key and returns the raw secret once for copying."""
    raw_key, key_hash, prefix = generate_api_key()
    rec = await create_api_key(
        db_path=settings.database_path,
        name=req.name,
        key_hash=key_hash,
        key_prefix=prefix,
        rpm_limit=req.rpm_limit,
        tpm_limit=req.tpm_limit,
        monthly_budget_usd=req.monthly_budget_usd,
        allowed_aliases=req.allowed_aliases,
    )
    res = rec.to_dict()
    res["secret"] = raw_key  # Returned once to display to the user
    return res


@router.patch("/keys/{key_id}", summary="Update Key Settings")
async def update_key(
    key_id: str,
    req: UpdateKeyRequest,
    _admin: Annotated[str, Depends(verify_admin_key)],
    settings: Annotated[Any, Depends(get_settings)],
) -> dict[str, Any]:
    """Updates enabled status, limits, budget, or allowed model aliases for a key."""
    existing = await get_api_key_by_id(settings.database_path, key_id)
    if not existing:
        raise HTTPException(status_code=404, detail="API Key not found")

    await update_api_key(
        db_path=settings.database_path,
        key_id=key_id,
        enabled=req.enabled,
        rpm_limit=req.rpm_limit,
        tpm_limit=req.tpm_limit,
        monthly_budget_usd=req.monthly_budget_usd,
        allowed_aliases=req.allowed_aliases,
    )
    refetched = await get_api_key_by_id(settings.database_path, key_id)
    return refetched.to_dict() if refetched else {}


@router.post("/keys/{key_id}/rotate", summary="Rotate Key Secret")
async def rotate_key(
    key_id: str,
    _admin: Annotated[str, Depends(verify_admin_key)],
    settings: Annotated[Any, Depends(get_settings)],
) -> dict[str, Any]:
    """Rotates secret hash and returns a new raw secret for the key."""
    existing = await get_api_key_by_id(settings.database_path, key_id)
    if not existing:
        raise HTTPException(status_code=404, detail="API Key not found")

    raw_key, new_hash, new_prefix = generate_api_key()
    await rotate_api_key(settings.database_path, key_id, new_hash, new_prefix)
    return {
        "status": "ok",
        "key_id": key_id,
        "new_prefix": new_prefix,
        "secret": raw_key,
    }


# ==============================================================================
# Config & Stats
# ==============================================================================


@router.get("/config", summary="View Router Model Aliases")
async def get_config(
    _admin: Annotated[str, Depends(verify_admin_key)],
    model_router: Annotated[Router, Depends(get_router)],
) -> dict[str, Any]:
    """Returns currently loaded model alias mappings and routing rules."""
    return {"aliases": model_router.aliases}


@router.post("/config/reload", summary="Hot Reload Router Config")
async def reload_config(
    _admin: Annotated[str, Depends(verify_admin_key)],
    model_router: Annotated[Router, Depends(get_router)],
) -> dict[str, str]:
    """Hot-reloads models.yaml from disk without server restart."""
    model_router.load_config()
    return {"status": "ok", "message": "Router configuration reloaded successfully."}


@router.get("/stats", summary="Get High-Level Dashboard Stats")
async def get_stats(
    _admin: Annotated[str, Depends(verify_admin_key)],
    settings: Annotated[Any, Depends(get_settings)],
) -> dict[str, Any]:
    """Returns aggregated metrics for top-line dashboard tiles."""
    summary = await get_stats_summary(settings.database_path)
    recent_logs = await get_recent_logs(settings.database_path, limit=20)
    provider_usage = await get_usage_by_provider(settings.database_path)

    total_reqs = summary.get("total_requests", 0)
    error_count = summary.get("error_count", 0)
    spend_today = summary.get("spend_today_usd", summary.get("spend_today", 0.0))
    hit_rate = summary.get("cache_hit_rate", 0.0)
    avg_lat = summary.get("avg_latency_ms", summary.get("avg_latency", 0.0)) or 0.0

    return {
        "total_requests": total_reqs,
        "total_cost_usd": spend_today,
        "requests_24h": total_reqs,
        "errors_24h": error_count,
        "spend_24h_usd": spend_today,
        "cache_hit_rate": hit_rate,
        "p95_latency_ms": avg_lat,
        "summary": summary,
        "recent_logs": recent_logs,
        "provider_usage": provider_usage,
        "top_keys_by_spend": [],
    }


# ==============================================================================
# Live SSE Event Stream (/admin/events)
# ==============================================================================


@router.get("/events", summary="Live SSE Stream of Gateway Events")
async def stream_admin_events(
    _admin: Annotated[str, Depends(verify_admin_key)],
    limit: int | None = None,
) -> StreamingResponse:
    """Streams live gateway events over Server-Sent Events (SSE) with periodic keepalive heartbeats."""
    event_bus = get_event_bus()

    async def sse_generator() -> AsyncIterator[str]:
        q = event_bus.subscribe()
        yield_count = 0
        try:
            # Yield initial connection confirmation
            yield f"event: ping\ndata: {json.dumps({'time': asyncio.get_event_loop().time()})}\n\n"
            yield_count += 1
            if limit is not None and yield_count >= limit:
                return

            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"event: {event.event_type}\ndata: {event.to_json()}\n\n"
                    q.task_done()
                    yield_count += 1
                    if limit is not None and yield_count >= limit:
                        return
                except TimeoutError:
                    yield ": ping\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            event_bus.unsubscribe(q)

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
