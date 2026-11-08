# Relay: System Architecture & Execution Plan

**Relay** is an OpenAI-compatible LLM Gateway with a Live Next.js Control Dashboard built with Python 3.12 (FastAPI), Redis 7, SQLite (`aiosqlite`), and Next.js 14+ (App Router, TypeScript, TailwindCSS). It is designed to be production-grade in resilience and observability, while keeping code structure readable, flat, and easy to explain for a CS student or interview candidate.

---

## 1. System Architecture Overview

```
                      +---------------------------------------+
                      |       Client / OpenAI SDK / UI        |
                      +-------------------+-------------------+
                                          | HTTP / SSE
                                          v
+-----------------------------------------------------------------------------------+
|                                 RELAY GATEWAY                                     |
|                                                                                   |
|  [ Middleware / Request Context ]                                                 |
|   - Request ID & JSON structured logging                                          |
|   - Latency tracking & Prometheus metrics                                         |
|   - Standardized OpenAI error JSON responses                                      |
|                                                                                   |
|  [ Auth & SQLite Data Layer (db.py) ]                                             |
|   - Bearer Token -> SHA-256 Key Hash lookup in SQLite                            |
|   - Budget check & model alias permission enforcement                             |
|                                                                                   |
|  [ Rate Limiting (No Lua) ]                                                       |
|   - Token Bucket: Redis Hash with WATCH / MULTI / EXEC                            |
|   - Sliding Window Log: Redis Sorted Set with MULTI / EXEC                         |
|   - RPM & TPM limits with pre-estimation (tiktoken) and reconciliation            |
|                                                                                   |
|  [ Response Cache (cache.py) ]                                                    |
|   - Exact-match SHA-256 key (model alias, messages, sampling params)              |
|   - Request coalescing (single-flight via in-memory asyncio.Event)                |
|   - Stream replay: cached response emitted as paced SSE chunks                    |
|                                                                                   |
|  [ Router & Alias Mapping (router.py) ]                                           |
|   - Alias Config: priority, round_robin, lowest_latency (EWMA), lowest_cost       |
|   - Circuit Breaker filter: bypass targets in OPEN state                          |
|                                                                                   |
|  [ Resilience Engine (circuit_breaker.py, retry.py) ]                             |
|   - Concurrency Semaphore per provider                                           |
|   - Configurable Connect, TTFT, and Total Timeouts                                |
|   - Exponential backoff with full jitter on retryable failures (5xx, 429, timeout)|
|   - Pre-token failure: transparent failover to next provider                      |
|   - Mid-stream failure: terminal SSE error event + circuit failure record         |
|   - Client disconnect cancellation                                                |
|                                                                                   |
|  [ Providers (providers/) ]                                                       |
|   - MockProvider (controllable at runtime for chaos testing)                      |
|   - OpenAIProvider, AnthropicProvider, GeminiProvider (native adapters)          |
+--------------------------+-------------------------------+------------------------+
                           |                               |
          Pub/Sub & State  |                               | Async Queue
                           v                               v
            +------------------------------+  +-------------------------------+
            |            REDIS             |  |        SQLITE (relay.db)      |
            | - Circuit Breaker State      |  | - api_keys table              |
            | - Failure timestamps         |  | - request_logs table          |
            | - EWMA Latency per Target    |  | - WAL mode enabled            |
            | - Rate limit keys (TB/SW)    |  | - Batched queue background    |
            | - Response Cache             |  |   writer off hot path         |
            | - Event Bus Pub/Sub Channel  |  +-------------------------------+
            +--------------+---------------+
                           |
                           v SSE Stream (/admin/events)
+-----------------------------------------------------------------------------------+
|                        NEXT.JS CONTROL DASHBOARD (dashboard/)                     |
|  - Live Request Feed (SSE)                - Provider Health & Circuit Cards       |
|  - Chaos Injection Panel (Live kill/lag)  - Traffic Share Failover Chart          |
|  - Interactive Playground (Cache test)    - Usage, Cost & Budget Progress         |
|  - Key Management                         - Config & Routing Inspection           |
+-----------------------------------------------------------------------------------+
```

---

## 2. Directory Structure

```
relay/
├── README.md
├── docs/
│   ├── PLAN.md                  # This plan
│   ├── ARCHITECTURE.md          # Deep dive with Mermaid diagrams & failure rules
│   ├── DECISIONS.md             # Decision records (ADRs)
│   ├── LEARNING.md              # Concepts, implementations, & interview Q&As
│   ├── API.md                   # Full API reference with curl examples
│   ├── DEMO.md                  # Step-by-step 3-minute demo script
│   ├── BENCHMARKS.md            # Real measured benchmarks with reproduction commands
│   └── HANDOFF.md               # Student walkthrough, limitations, 5 design defenses
├── backend/
│   ├── requirements.txt         # Pinned backend dependencies
│   ├── src/relay/
│   │   ├── __init__.py
│   │   ├── main.py              # FastAPI app, lifespan, CORS, middleware, mounts
│   │   ├── config.py            # Pydantic v2 settings with env defaults
│   │   ├── db.py                # ALL SQLite queries, table init, and batch queue writer
│   │   ├── auth.py              # SHA-256 API key authentication & admin token check
│   │   ├── router.py            # Alias loader & 4 routing strategies
│   │   ├── retry.py             # Backoff calculation, full jitter, retryable rules
│   │   ├── circuit_breaker.py   # Redis-backed circuit breaker state machine
│   │   ├── cache.py             # Exact-match Redis cache + single-flight coalescing
│   │   ├── ratelimit.py         # Token Bucket & Sliding Window Log (No Lua)
│   │   ├── cost.py              # Pricing table, token counting/estimation, spend
│   │   ├── events.py            # Event bus (local asyncio + Redis pub/sub)
│   │   ├── metrics.py           # Prometheus metrics definitions
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   ├── openai_compat.py # /v1/chat/completions, /v1/models
│   │   │   ├── admin.py         # /admin/providers, /admin/chaos, /admin/keys, /admin/stats, /admin/events
│   │   │   └── health.py        # /healthz, /readyz, /metrics
│   │   └── providers/
│   │       ├── __init__.py
│   │       ├── base.py          # Provider base class & completion models
│   │       ├── mock.py          # MockProvider with chaos knobs
│   │       ├── openai.py        # OpenAI adapter
│   │       ├── anthropic.py     # Anthropic Claude adapter
│   │       └── gemini.py        # Google Gemini adapter
│   └── tests/
│       ├── conftest.py
│       ├── unit/
│       │   ├── test_circuit_breaker.py
│       │   ├── test_retry.py
│       │   ├── test_router.py
│       │   ├── test_cache.py
│       │   ├── test_ratelimit.py
│       │   ├── test_cost.py
│       │   ├── test_db.py
│       │   └── test_providers.py
│       └── integration/
│           ├── test_openai_compat.py
│           ├── test_resilience_failover.py
│           ├── test_streaming_failure.py
│           ├── test_ratelimit_concurrency.py
│           └── test_admin_and_chaos.py
├── dashboard/                   # Next.js App Router app
│   ├── package.json
│   ├── tsconfig.json
│   ├── tailwind.config.ts
│   ├── src/
│   │   ├── app/
│   │   │   ├── layout.tsx
│   │   │   ├── page.tsx         # Live dashboard & Chaos section
│   │   │   ├── playground/page.tsx
│   │   │   ├── analytics/page.tsx
│   │   │   ├── keys/page.tsx
│   │   │   └── config/page.tsx
│   │   ├── components/          # Reusable components
│   │   ├── lib/                 # API client, SSE hooks, formatting utils
│   │   └── types/               # TypeScript interfaces
├── scripts/
│   ├── load_generator.py        # Realistic async traffic generator
│   └── seed.py                  # Seed demo keys and alias configs
├── docker-compose.yml           # Gateway, Redis, Dashboard
├── .env.example
└── .github/workflows/ci.yml
```

---

## 3. SQLite Data Model (`backend/src/relay/db.py`)

No ORM or migrations. Created with `CREATE TABLE IF NOT EXISTS` at startup in WAL mode (`PRAGMA journal_mode=WAL;`):

### 3.1 `api_keys`
- `id`: TEXT PRIMARY KEY (UUID string)
- `name`: TEXT NOT NULL
- `key_hash`: TEXT NOT NULL UNIQUE (SHA-256 of plain secret)
- `key_prefix`: TEXT NOT NULL (e.g. `rl_live_9f8a`)
- `rpm_limit`: INTEGER NOT NULL DEFAULT 60
- `tpm_limit`: INTEGER NOT NULL DEFAULT 60000
- `monthly_budget_usd`: REAL NOT NULL DEFAULT 50.0
- `allowed_aliases`: TEXT NOT NULL DEFAULT '["*"]' (JSON array of allowed aliases)
- `enabled`: INTEGER NOT NULL DEFAULT 1 (1 = active, 0 = disabled)
- `created_at`: TEXT NOT NULL (ISO-8601 string)

### 3.2 `request_logs`
- `id`: TEXT PRIMARY KEY (UUID string)
- `timestamp`: TEXT NOT NULL (ISO-8601 string)
- `api_key_id`: TEXT (references api_keys.id)
- `alias`: TEXT NOT NULL
- `provider`: TEXT NOT NULL
- `upstream_model`: TEXT NOT NULL
- `status`: INTEGER NOT NULL
- `attempts`: INTEGER NOT NULL DEFAULT 1
- `cache_status`: TEXT NOT NULL ('HIT', 'MISS', 'BYPASS')
- `latency_ms`: REAL NOT NULL
- `ttft_ms`: REAL
- `tokens_in`: INTEGER NOT NULL DEFAULT 0
- `tokens_out`: INTEGER NOT NULL DEFAULT 0
- `cost_usd`: REAL NOT NULL DEFAULT 0.0
- `error_type`: TEXT

---

## 4. API Surface

### 4.1 OpenAI Compatible API (`/v1`)
- `POST /v1/chat/completions`: Supports standard OpenAI chat completions body.
  - Non-streaming: JSON response with `id`, `model`, `choices`, `usage`.
  - Streaming: SSE `data: {...}\n\n` chunks ending with `data: [DONE]`.
  - Headers: `X-Relay-Request-Id`, `X-Relay-Provider`, `X-Relay-Cache`, `X-Relay-Attempts`, `X-Relay-Latency-Ms`.
  - Errors: `{"error": {"message": "...", "type": "...", "code": ...}}`.
- `GET /v1/models`: Returns list of available model aliases formatted as OpenAI model objects.

### 4.2 Admin API (`/admin`, requires `Bearer <RELAY_ADMIN_KEY>`)
- `GET /admin/providers`: Provider statuses, circuit states, EWMA latencies, in-flight counts, recent error rates.
- `POST /admin/chaos`: Update chaos parameters for mock providers (`provider`, `is_down`, `latency_ms`, `error_rate`, `fail_after_tokens`, `slow_ttft_ms`).
- `POST /admin/chaos/reset`: Reset all mock chaos controls.
- `GET /admin/keys`: List all API keys and their usage.
- `POST /admin/keys`: Create a new API key.
- `PATCH /admin/keys/{id}`: Enable/disable key, update rate limits or budget.
- `POST /admin/keys/{id}/rotate`: Generate new secret for existing key ID.
- `GET /admin/config`: Return current alias routing rules.
- `POST /admin/config/reload`: Hot-reload YAML configuration.
- `GET /admin/stats`: Aggregate dashboard stats (RPS, error rate, p50/p95/p99 latency, cache hit rate, today's spend).
- `GET /admin/events`: SSE stream emitting system events with heartbeat keepalive.

### 4.3 Health & Metrics
- `GET /healthz`: Fast liveness check (200 OK).
- `GET /readyz`: Readiness check verifying Redis connectivity and SQLite accessibility.
- `GET /metrics`: Prometheus formatted metrics.

---

## 5. Milestone Breakdown & Acceptance Criteria

- **M0: Plan and scaffold.**
  - `docs/PLAN.md`, `docs/DECISIONS.md`, `docs/LEARNING.md` scaffolded.
  - `backend/` with `requirements.txt`, `src/relay/main.py`, `src/relay/config.py`, `src/relay/db.py`, `src/relay/api/health.py`.
  - SQLite initialization on startup creating `api_keys` and `request_logs` with WAL mode.
  - `docker-compose.yml`, `.env.example`, ruff and mypy configuration, initial tests.
  - *Acceptance:* Initial test suite passes, `ruff` & `mypy` clean, `/healthz` and `/readyz` work.

- **M1: Core gateway with Mock provider.**
  - Provider base class, `MockProvider` with realistic streamed and non-streamed responses.
  - Config loader for `config/models.yaml`.
  - `/v1/chat/completions` (streaming & non-streaming) and `/v1/models`.
  - API key auth with SHA-256 hashes in SQLite.
  - Structured JSON logs with request IDs.
  - *Acceptance:* Official OpenAI Python SDK works against Relay (streaming & non-streaming); unit/integration tests pass.

- **M2: Resilience.**
  - Connect, TTFT, and total timeouts.
  - Exponential backoff with full jitter for retryable errors.
  - Redis-backed Circuit Breaker (CLOSED, OPEN, HALF_OPEN) with event emission.
  - Transparent failover before first token.
  - Terminal SSE error event on mid-stream failure.
  - Request cancellation on client disconnect; provider concurrency semaphore; graceful shutdown.
  - *Acceptance:* Tests prove transparent failover on forced provider failure, correct circuit state changes, and mid-stream error behavior.

- **M3: Routing, caching, rate limiting.**
  - 4 routing strategies: `priority`, `round_robin`, `lowest_latency` (EWMA in Redis), `lowest_cost`.
  - Exact-match response cache in Redis with request coalescing (`asyncio.Event`). Cached streaming replay.
  - Token Bucket (`WATCH/MULTI/EXEC`) & Sliding Window Log (`MULTI/EXEC` sorted set) without Lua.
  - Per-key RPM and TPM enforcement with 429 status and headers.
  - *Acceptance:* Concurrency tests prove rate limits hold across two app instances; cache coalescing verified.

- **M4: Usage, cost, events, admin API.**
  - Cost computation via pricing table; token estimation with `tiktoken`.
  - Async bounded queue batch logger to SQLite.
  - Monthly budget enforcement (reject with 402/429 when budget exceeded).
  - Prometheus metrics (`/metrics`).
  - Redis pub/sub event bus with `/admin/events` SSE stream.
  - Full `/admin` endpoints (providers, chaos, keys, config, stats).
  - *Acceptance:* Metrics exposed; events stream to client; budget cutoff verified.

- **M5: Real provider adapters.**
  - `OpenAIProvider`, `AnthropicProvider`, `GeminiProvider` with request/response translation.
  - Fixture-based tests via `respx` (no live keys required).
  - App starts cleanly without real keys.
  - *Acceptance:* Provider translation tests pass for both streaming and non-streaming.

- **M6: Next.js dashboard.**
  - Next.js (App Router) + TypeScript + TailwindCSS in `dashboard/`.
  - Live page (SSE request feed, provider cards with circuit colors, failover traffic chart, top metric tiles).
  - Chaos panel (Kill/Restore toggles, latency/error sliders, fail-mid-stream toggle, Reset all).
  - Playground (streaming chat with provider, cache HIT/MISS, latency stats, Send Again button).
  - Usage & Cost page (spend/tokens charts, budget progress bars).
  - Keys page (create, rotate, disable, secret modal).
  - Config page (inspect aliases & reload button).
  - *Acceptance:* Browser testing of the full chaos demo flow; screenshot capture; no console errors.

- **M7: Load test, docs, CI polish.**
  - `scripts/load_generator.py` with RPS, stream ratio, cache repeat ratio, scenarios.
  - `scripts/seed.py` for demo keys and initial data.
  - Measured benchmarks in `docs/BENCHMARKS.md`.
  - `README.md`, `ARCHITECTURE.md`, `LEARNING.md`, `API.md`, `DEMO.md`, `HANDOFF.md`.
  - Full CI workflow verified.
  - *Acceptance:* Complete end-to-end demo script verified; all benchmark numbers measured; complete handoff guide.

- **M8: Stretch goals.**
  - Prometheus and Grafana provisioned dashboard in `deploy/`.

