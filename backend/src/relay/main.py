"""Main application factory and lifespan wiring for Relay LLM Gateway."""

import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from relay.api.admin import router as admin_router
from relay.api.health import router as health_router
from relay.api.openai_compat import router as openai_router
from relay.config import get_settings
from relay.db import get_log_writer, init_db
from relay.events import get_event_bus
from relay.metrics import get_metrics_content

# Configure structured JSON / standard logging
logging.basicConfig(
    level=logging.INFO,
    format='{"time": "%(asctime)s", "level": "%(levelname)s", "logger": "%(name)s", "message": "%(message)s"}',
)
logger = logging.getLogger("relay")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manages gateway lifecycle: initializes SQLite database and starts log batch writer and event bus."""
    settings = get_settings()
    logger.info("Relay gateway starting up...")

    # Initialize SQLite schema and enable WAL mode
    await init_db(settings.database_path, settings.relay_admin_key)

    # Start off-the-hot-path background log batch writer
    log_writer = get_log_writer(settings.database_path)
    log_writer.start()

    # Start EventBus Redis listener if configured
    event_bus = get_event_bus(redis_url=settings.redis_url)
    await event_bus.start()

    yield

    # Shutdown: flush remaining logs and close background tasks
    logger.info("Relay gateway shutting down...")
    await log_writer.stop()
    await event_bus.stop()


def create_app() -> FastAPI:
    """Creates and configures the FastAPI application."""
    app = FastAPI(
        title="Relay LLM Gateway",
        description="OpenAI-compatible LLM Gateway with Live Control Dashboard",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS configuration to allow the Next.js dashboard to communicate directly
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next: Any) -> Response:
        """Attaches a unique Request ID and tracks request duration."""
        request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        request.state.request_id = request_id
        start_time = time.perf_counter()

        response = await call_next(request)

        duration_ms = (time.perf_counter() - start_time) * 1000.0
        response.headers["X-Relay-Request-Id"] = request_id
        response.headers["X-Relay-Latency-Ms"] = f"{duration_ms:.2f}"
        return response

    # Standardize HTTP exception responses into OpenAI error shape
    @app.exception_handler(HTTPException)
    async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict) and "error" in detail:
            content = detail
        else:
            content = {
                "error": {
                    "message": str(detail),
                    "type": "invalid_request_error" if exc.status_code < 500 else "api_error",
                    "code": exc.status_code,
                }
            }
        return JSONResponse(status_code=exc.status_code, content=content, headers=exc.headers)

    # Standardize Request validation errors (422) into OpenAI error shape
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        first_err = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first_err.get("loc", []))
        msg = first_err.get("msg", "Validation error")
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": {
                    "message": f"Validation error at {loc}: {msg}",
                    "type": "invalid_request_error",
                    "param": loc,
                    "code": "validation_error",
                }
            },
        )

    # Prometheus metrics endpoint
    @app.get("/metrics", summary="Prometheus Metrics Exposition")
    async def metrics_endpoint() -> Response:
        content, content_type = get_metrics_content()
        return Response(content=content, media_type=content_type)

    # Mount routers
    app.include_router(health_router)
    app.include_router(openai_router)
    app.include_router(admin_router)

    return app


app = create_app()
