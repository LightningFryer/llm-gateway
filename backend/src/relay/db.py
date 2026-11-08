"""Single-file SQLite database operations for Relay.

All database logic lives here: table initialization, WAL mode configuration,
API key management, analytics aggregation, and the asynchronous batch log writer.
No ORM or migration tools are used.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

import aiosqlite

logger = logging.getLogger("relay.db")

# Table Definitions
SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS api_keys (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    key_hash TEXT NOT NULL UNIQUE,
    key_prefix TEXT NOT NULL,
    rpm_limit INTEGER NOT NULL DEFAULT 60,
    tpm_limit INTEGER NOT NULL DEFAULT 60000,
    monthly_budget_usd REAL NOT NULL DEFAULT 50.0,
    allowed_aliases TEXT NOT NULL DEFAULT '["*"]',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash);

CREATE TABLE IF NOT EXISTS request_logs (
    id TEXT PRIMARY KEY,
    timestamp TEXT NOT NULL,
    api_key_id TEXT,
    alias TEXT NOT NULL,
    provider TEXT NOT NULL,
    upstream_model TEXT NOT NULL,
    status INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 1,
    cache_status TEXT NOT NULL,
    latency_ms REAL NOT NULL,
    ttft_ms REAL,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    error_type TEXT,
    FOREIGN KEY (api_key_id) REFERENCES api_keys(id)
);

CREATE INDEX IF NOT EXISTS idx_request_logs_timestamp ON request_logs(timestamp);
CREATE INDEX IF NOT EXISTS idx_request_logs_key_id ON request_logs(api_key_id);
CREATE INDEX IF NOT EXISTS idx_request_logs_provider ON request_logs(provider);
"""


@dataclass
class ApiKeyRecord:
    """Represents an API key record stored in SQLite."""

    id: str
    name: str
    key_hash: str
    key_prefix: str
    rpm_limit: int
    tpm_limit: int
    monthly_budget_usd: float
    allowed_aliases: list[str]
    enabled: bool
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        """Convert record to dictionary for JSON serialization."""
        data = asdict(self)
        return data


@dataclass
class RequestLogRecord:
    """Represents an audit log row for a completed or failed request."""

    id: str
    timestamp: str
    api_key_id: str | None
    alias: str
    provider: str
    upstream_model: str
    status: int
    attempts: int
    cache_status: str
    latency_ms: float
    ttft_ms: float | None
    tokens_in: int
    tokens_out: int
    cost_usd: float
    error_type: str | None


async def init_db(db_path: str, admin_key: str = "relay_admin_secret_key_12345") -> None:
    """Initializes SQLite database with WAL mode and tables, and seeds admin key."""
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA_SQL)
        admin_hash = hashlib.sha256(admin_key.strip().encode("utf-8")).hexdigest()
        now = datetime.now(UTC).isoformat()
        await db.execute(
            """
            INSERT OR IGNORE INTO api_keys (
                id, name, key_hash, key_prefix, rpm_limit, tpm_limit,
                monthly_budget_usd, allowed_aliases, enabled, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                "admin-master-key",
                "Admin Master Key",
                admin_hash,
                "admin",
                100000,
                100000000,
                999999.0,
                '["*"]',
                now,
            ),
        )
        await db.commit()
    logger.info("SQLite database initialized successfully at %s (WAL mode enabled)", db_path)


async def check_db_health(db_path: str) -> bool:
    """Performs a quick read query to verify SQLite health."""
    try:
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("SELECT 1")
            row = await cursor.fetchone()
            return row is not None and row[0] == 1
    except Exception as e:
        logger.error("SQLite health check failed: %s", e)
        return False


# ==============================================================================
# API Key Queries
# ==============================================================================


async def create_api_key(
    db_path: str,
    name: str,
    key_hash: str,
    key_prefix: str,
    rpm_limit: int = 60,
    tpm_limit: int = 60000,
    monthly_budget_usd: float = 50.0,
    allowed_aliases: list[str] | None = None,
) -> ApiKeyRecord:
    """Inserts a new hashed API key into SQLite."""
    key_id = str(uuid.uuid4())
    created_at = datetime.now(UTC).isoformat()
    aliases = allowed_aliases or ["*"]
    aliases_json = json.dumps(aliases)

    query = """
    INSERT INTO api_keys (
        id, name, key_hash, key_prefix, rpm_limit, tpm_limit,
        monthly_budget_usd, allowed_aliases, enabled, created_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
    """
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            query,
            (
                key_id,
                name,
                key_hash,
                key_prefix,
                rpm_limit,
                tpm_limit,
                monthly_budget_usd,
                aliases_json,
                created_at,
            ),
        )
        await db.commit()

    return ApiKeyRecord(
        id=key_id,
        name=name,
        key_hash=key_hash,
        key_prefix=key_prefix,
        rpm_limit=rpm_limit,
        tpm_limit=tpm_limit,
        monthly_budget_usd=monthly_budget_usd,
        allowed_aliases=aliases,
        enabled=True,
        created_at=created_at,
    )


def _row_to_api_key(row: sqlite3.Row | tuple[Any, ...]) -> ApiKeyRecord:
    """Converts a SQLite row tuple into an ApiKeyRecord."""
    allowed = json.loads(row[7]) if isinstance(row[7], str) else ["*"]
    return ApiKeyRecord(
        id=str(row[0]),
        name=str(row[1]),
        key_hash=str(row[2]),
        key_prefix=str(row[3]),
        rpm_limit=int(row[4]),
        tpm_limit=int(row[5]),
        monthly_budget_usd=float(row[6]),
        allowed_aliases=allowed,
        enabled=bool(row[8]),
        created_at=str(row[9]),
    )


async def get_api_key_by_hash(db_path: str, key_hash: str) -> ApiKeyRecord | None:
    """Looks up an API key by SHA-256 hash."""
    query = """
    SELECT id, name, key_hash, key_prefix, rpm_limit, tpm_limit,
           monthly_budget_usd, allowed_aliases, enabled, created_at
    FROM api_keys
    WHERE key_hash = ?
    """
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(query, (key_hash,))
        row = await cursor.fetchone()
        if not row:
            return None
        return _row_to_api_key(row)


async def get_api_key_by_id(db_path: str, key_id: str) -> ApiKeyRecord | None:
    """Looks up an API key by ID."""
    query = """
    SELECT id, name, key_hash, key_prefix, rpm_limit, tpm_limit,
           monthly_budget_usd, allowed_aliases, enabled, created_at
    FROM api_keys
    WHERE id = ?
    """
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(query, (key_id,))
        row = await cursor.fetchone()
        if not row:
            return None
        return _row_to_api_key(row)


async def list_api_keys(db_path: str) -> list[ApiKeyRecord]:
    """Returns all configured API keys."""
    query = """
    SELECT id, name, key_hash, key_prefix, rpm_limit, tpm_limit,
           monthly_budget_usd, allowed_aliases, enabled, created_at
    FROM api_keys
    ORDER BY created_at DESC
    """
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(query)
        rows = await cursor.fetchall()
        return [_row_to_api_key(r) for r in rows]


async def update_api_key(
    db_path: str,
    key_id: str,
    enabled: bool | None = None,
    rpm_limit: int | None = None,
    tpm_limit: int | None = None,
    monthly_budget_usd: float | None = None,
    allowed_aliases: list[str] | None = None,
) -> bool:
    """Updates key configuration fields."""
    updates: list[str] = []
    params: list[Any] = []

    if enabled is not None:
        updates.append("enabled = ?")
        params.append(1 if enabled else 0)
    if rpm_limit is not None:
        updates.append("rpm_limit = ?")
        params.append(rpm_limit)
    if tpm_limit is not None:
        updates.append("tpm_limit = ?")
        params.append(tpm_limit)
    if monthly_budget_usd is not None:
        updates.append("monthly_budget_usd = ?")
        params.append(monthly_budget_usd)
    if allowed_aliases is not None:
        updates.append("allowed_aliases = ?")
        params.append(json.dumps(allowed_aliases))

    if not updates:
        return False

    params.append(key_id)
    query = f"UPDATE api_keys SET {', '.join(updates)} WHERE id = ?"
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(query, tuple(params))
        await db.commit()
        return cursor.rowcount > 0


async def rotate_api_key(db_path: str, key_id: str, new_hash: str, new_prefix: str) -> bool:
    """Rotates secret hash and prefix for an existing key ID."""
    query = "UPDATE api_keys SET key_hash = ?, key_prefix = ? WHERE id = ?"
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(query, (new_hash, new_prefix, key_id))
        await db.commit()
        return cursor.rowcount > 0


# ==============================================================================
# Analytics & Spend Queries
# ==============================================================================


async def get_monthly_spend(db_path: str, api_key_id: str) -> float:
    """Calculates total spend in USD for this API key in the current calendar month."""
    start_of_month = (
        datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    )
    query = """
    SELECT COALESCE(SUM(cost_usd), 0.0)
    FROM request_logs
    WHERE api_key_id = ? AND timestamp >= ?
    """
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(query, (api_key_id, start_of_month))
        row = await cursor.fetchone()
        return float(row[0]) if row and row[0] is not None else 0.0


async def get_stats_summary(db_path: str) -> dict[str, Any]:
    """Calculates high-level system metrics for the dashboard."""
    start_of_today = (
        datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    )
    query = """
    SELECT
        COUNT(*) as total_requests,
        SUM(CASE WHEN status >= 400 THEN 1 ELSE 0 END) as error_count,
        SUM(CASE WHEN cache_status = 'HIT' THEN 1 ELSE 0 END) as cache_hits,
        COALESCE(SUM(cost_usd), 0.0) as spend_today,
        AVG(latency_ms) as avg_latency
    FROM request_logs
    WHERE timestamp >= ?
    """
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(query, (start_of_today,))
        row = await cursor.fetchone()
        if not row:
            return {
                "total_requests": 0,
                "error_rate": 0.0,
                "cache_hit_rate": 0.0,
                "spend_today_usd": 0.0,
                "avg_latency_ms": 0.0,
            }

        total = row[0] or 0
        errors = row[1] or 0
        hits = row[2] or 0
        spend = float(row[3] or 0.0)
        avg_lat = float(row[4] or 0.0)

        return {
            "total_requests": total,
            "error_rate": (errors / total) if total > 0 else 0.0,
            "cache_hit_rate": (hits / total) if total > 0 else 0.0,
            "spend_today_usd": spend,
            "avg_latency_ms": round(avg_lat, 2),
        }


async def get_recent_logs(db_path: str, limit: int = 50) -> list[dict[str, Any]]:
    """Returns the most recent request logs for the dashboard feed."""
    query = """
    SELECT id, timestamp, api_key_id, alias, provider, upstream_model,
           status, attempts, cache_status, latency_ms, ttft_ms,
           tokens_in, tokens_out, cost_usd, error_type
    FROM request_logs
    ORDER BY timestamp DESC
    LIMIT ?
    """
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(query, (limit,))
        rows = await cursor.fetchall()
        results: list[dict[str, Any]] = []
        for r in rows:
            results.append(
                {
                    "id": r[0],
                    "timestamp": r[1],
                    "api_key_id": r[2],
                    "alias": r[3],
                    "provider": r[4],
                    "upstream_model": r[5],
                    "status": r[6],
                    "attempts": r[7],
                    "cache_status": r[8],
                    "latency_ms": r[9],
                    "ttft_ms": r[10],
                    "tokens_in": r[11],
                    "tokens_out": r[12],
                    "cost_usd": r[13],
                    "error_type": r[14],
                }
            )
        return results


async def get_usage_by_provider(db_path: str) -> list[dict[str, Any]]:
    """Aggregates request count and spend grouped by provider."""
    query = """
    SELECT provider, COUNT(*), SUM(cost_usd), AVG(latency_ms)
    FROM request_logs
    GROUP BY provider
    """
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(query)
        rows = await cursor.fetchall()
        return [
            {
                "provider": r[0],
                "count": r[1],
                "spend_usd": float(r[2] or 0.0),
                "avg_latency_ms": round(float(r[3] or 0.0), 2),
            }
            for r in rows
        ]


# ==============================================================================
# Async Bounded Queue & Batch Writer (Off the Hot Path)
# ==============================================================================


class AsyncLogWriter:
    """Drains request logs from an in-memory queue and inserts them into SQLite in batches."""

    def __init__(
        self,
        db_path: str,
        maxsize: int = 10000,
        batch_size: int = 100,
        flush_interval: float = 0.5,
    ) -> None:
        self.db_path = db_path
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.queue: asyncio.Queue[RequestLogRecord] = asyncio.Queue(maxsize=maxsize)
        self._worker_task: asyncio.Task[None] | None = None
        self._stopping = False
        self.dropped_logs_count = 0

    def enqueue(self, record: RequestLogRecord) -> bool:
        """Pushes a log record into the queue. Drops if saturated to protect request latency."""
        try:
            self.queue.put_nowait(record)
            return True
        except asyncio.QueueFull:
            self.dropped_logs_count += 1
            logger.warning(
                "Request log queue full! Dropped log id=%s (total dropped=%d)",
                record.id,
                self.dropped_logs_count,
            )
            return False

    def start(self) -> None:
        """Starts the background batch writing loop."""
        if self._worker_task is None:
            self._stopping = False
            self._worker_task = asyncio.create_task(self._batch_drain_loop())
            logger.info("AsyncLogWriter background task started")

    async def stop(self) -> None:
        """Stops the worker and flushes any remaining items in the queue."""
        self._stopping = True
        if self._worker_task:
            await self._worker_task
            self._worker_task = None
        # Flush remaining
        await self._flush_current_batch()
        logger.info("AsyncLogWriter stopped cleanly")

    async def _batch_drain_loop(self) -> None:
        """Loop reading items and flushing periodically."""
        while not self._stopping:
            try:
                await asyncio.sleep(self.flush_interval)
                await self._flush_current_batch()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error during log batch flush: %s", e)

    async def _flush_current_batch(self) -> None:
        """Pulls up to batch_size items from the queue and inserts them."""
        batch: list[RequestLogRecord] = []
        while len(batch) < self.batch_size and not self.queue.empty():
            try:
                batch.append(self.queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        if not batch:
            return

        insert_sql = """
        INSERT INTO request_logs (
            id, timestamp, api_key_id, alias, provider, upstream_model,
            status, attempts, cache_status, latency_ms, ttft_ms,
            tokens_in, tokens_out, cost_usd, error_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        rows = [
            (
                r.id,
                r.timestamp,
                r.api_key_id,
                r.alias,
                r.provider,
                r.upstream_model,
                r.status,
                r.attempts,
                r.cache_status,
                r.latency_ms,
                r.ttft_ms,
                r.tokens_in,
                r.tokens_out,
                r.cost_usd,
                r.error_type,
            )
            for r in batch
        ]

        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.executemany(insert_sql, rows)
                await db.commit()
            for _ in batch:
                self.queue.task_done()
        except Exception as e:
            logger.error("Failed to commit log batch of %d items: %s", len(batch), e)


# Global singleton instance of log writer
_log_writer: AsyncLogWriter | None = None


def get_log_writer(db_path: str = "relay.db") -> AsyncLogWriter:
    """Returns or creates the global AsyncLogWriter singleton."""
    global _log_writer
    if _log_writer is None:
        _log_writer = AsyncLogWriter(db_path=db_path)
    return _log_writer
