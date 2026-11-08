"""Main application factory and lifespan wiring for Relay LLM Gateway."""

import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from relay.api.health import router as health_router
from relay.config import get_settings
from relay.db import get_log_writer, init_db

# Configure structured JSON / standard logging
logging.basicConfig(
    level=logging.INFO,
    format='{"time": "%(asctime)s", "level": "%(levelname)s", "logger": "%(name)s", "message": "%(message)s"}',
)
logger = logging.getLogger("relay")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manages gateway lifecycle: initializes SQLite database and starts log batch writer."""
    settings = get_settings()
    logger.info("Relay gateway starting up...")

    # Initialize SQLite schema and enable WAL mode
    await init_db(settings.database_path)

    # Start off-the-hot-path background log batch writer
    log_writer = get_log_writer(settings.database_path)
    log_writer.start()

    yield

    # Shutdown: flush remaining logs and close background tasks
    logger.info("Relay gateway shutting down...")
    await log_writer.stop()


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
    async def request_context_middleware(request: Request, call_next: Any) -> Response:  # type: ignore
        """Attaches a unique Request ID and tracks request duration."""
        request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        request.state.request_id = request_id
        start_time = time.perf_counter()

        response = await call_next(request)

        duration_ms = (time.perf_counter() - start_time) * 1000.0
        response.headers["X-Relay-Request-Id"] = request_id
        response.headers["X-Relay-Latency-Ms"] = f"{duration_ms:.2f}"
        return response

    # Mount routers
    app.include_router(health_router)

    return app


app = create_app()
