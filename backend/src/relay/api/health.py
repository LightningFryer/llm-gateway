"""Health check endpoints for Relay."""

from typing import Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Response, status

from relay.config import get_settings
from relay.db import check_db_health

router = APIRouter(tags=["Health"])


@router.get("/healthz", summary="Liveness Probe")
async def healthz() -> dict[str, str]:
    """Instant liveness check confirming the HTTP server is running."""
    return {"status": "ok", "service": "relay"}


@router.get("/readyz", summary="Readiness Probe")
async def readyz(response: Response) -> dict[str, Any]:
    """Readiness probe checking database access and Redis connectivity.

    If Redis is down, Relay operates in degraded mode (in-memory rate limits & cache fallback).
    """
    settings = get_settings()
    db_healthy = await check_db_health(settings.database_path)

    redis_status = "ok"
    try:
        r = aioredis.from_url(settings.redis_url, socket_timeout=1.0)
        await r.ping()
        await r.aclose()
    except Exception:
        redis_status = "degraded"

    overall_healthy = db_healthy
    if not overall_healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ok" if overall_healthy else "unavailable",
        "components": {
            "database": "ok" if db_healthy else "unhealthy",
            "redis": redis_status,
        },
    }
