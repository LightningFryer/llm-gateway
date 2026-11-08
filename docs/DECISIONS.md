# Relay: Architecture Decision Records (ADRs)

This document records the architectural and engineering decisions made during the design and development of Relay, following the format: Context, Decision, Alternatives Considered, Tradeoffs.

---

## ADR-001: Pure Python Redis Implementation (No Lua Scripts)

- **Context**: The gateway coordinates distributed state (token bucket rate limits, sliding window logs, circuit breaker status, EWMA latencies) across multiple gateway replicas using Redis 7.
- **Decision**: Avoid all Redis Lua scripting (`EVAL` / `EVALSHA`) and Redis Functions. Implement all atomic operations and distributed synchronization using standard redis-py transactions (`WATCH / MULTI / EXEC`), atomic commands (`INCR`, `ZADD`, `ZREMRANGEBYSCORE`), and bounded optimistic concurrency retry loops.
- **Alternatives Considered**:
  - *Redis Lua scripts*: Would execute atomically in a single round-trip, but hide business logic in script strings, complicate unit testing without a live Redis server, and violate project rule #7.
  - *Distributed locks (Redlock)*: Heavyweight overhead for simple counter increments and sliding windows.
- **Tradeoffs**: Requires handling `WatchError` retries under high concurrency for the token bucket, resulting in slight retry overhead during intense bursts. However, it keeps 100% of the business logic in type-checked, testable Python code that works identically across fake and real Redis implementations.

---

## ADR-002: Dual Rate Limiting Algorithms (Token Bucket vs. Sliding Window Log)

- **Context**: Different API clients need different traffic shaping behaviors: token-based rate limits for payload volume (TPM) and request rate limits (RPM) for API frequency.
- **Decision**: Provide two pluggable Redis-backed implementations:
  1. *Token Bucket*: Uses a Redis Hash storing `tokens` and `last_updated_timestamp` with `WATCH / MULTI / EXEC`. Best for allowing controlled bursts up to bucket capacity while smoothly refilling.
  2. *Sliding Window Log*: Uses a Redis Sorted Set storing request timestamps with `ZREMRANGEBYSCORE`, `ZADD`, and `ZCARD` in a `MULTI / EXEC` pipeline. Best for strict, zero-burst compliance over rolling 60-second intervals.
- **Alternatives Considered**:
  - *Fixed Window Counter*: Simple `INCR` with TTL, but suffers from boundary double-burst vulnerability (2x allowed traffic at window boundaries).
  - *Sliding Window Counter*: Approximates rolling windows via weighted segments, but sacrifices strict precision.
- **Tradeoffs**: The sliding window log consumes more memory ($O(N)$ elements in the sorted set where $N$ is requests in the window), so it is ideal for lower-volume RPM limits. Token bucket has $O(1)$ constant memory and accommodates TPM tracking, but requires optimistic lock retries under contention.

---

## ADR-003: Streaming Failure Boundary: Transparent Pre-Token Failover vs. Mid-Stream Error Event

- **Context**: When streaming responses to clients (`stream=True`), an upstream provider may fail either before the first chunk is received or halfway through generating tokens.
- **Decision**: Establish an explicit two-phase streaming failure contract:
  1. *Pre-first-token failure*: If the selected provider drops the connection, times out, or returns an HTTP error before yielding any token, Relay catches the error, records a circuit breaker failure, and immediately falls over to the next candidate provider in the alias plan. The client never sees the failure.
  2. *Mid-stream failure*: Once even a single chunk has been flushed to the client over SSE, transparent provider switching is impossible (as providers use different model tokens and contexts, risking duplicate or disjoint sentences). Relay flushes an explicit OpenAI-style SSE error event (`event: error`), terminates the stream cleanly with `data: [DONE]`, records the failure against the circuit breaker, and logs the incident.
- **Alternatives Considered**:
  - *Buffering the entire stream*: Eliminates mid-stream failover problems, but destroys Time-To-First-Token (TTFT) latency and user interactivity.
  - *Silently dropping the connection mid-stream*: Leaves the client hanging or crashing without structured error details.
- **Tradeoffs**: Clients must handle mid-stream SSE errors gracefully, but in exchange they receive genuine streaming TTFT and transparent protection against pre-token provider outages.

---

## ADR-004: Decoupled Asynchronous DB Logging with Bounded In-Memory Queue

- **Context**: Every request generates a detailed audit log row (latency, tokens, cost, cache status, attempts, error type) for storage and analytics. Writing synchronously to the database on the request hot path introduces disk latency into every API response.
- **Decision**: Implement an asynchronous batch logger using an `asyncio.Queue` with a bounded size (10,000 items) and a background worker task in `db.py` that flushes batches (up to 100 items or every 500ms) using SQLite `executemany`.
- **Alternatives Considered**:
  - *Synchronous DB write per request*: Severely degrades p99 latency and bottlenecks gateway throughput on database write I/O.
  - *Writing logs to Redis stream first*: Adds Redis write amplification and operational complexity.
- **Tradeoffs**: If the gateway process crashes abruptly, up to 500ms of in-flight audit logs might be lost. If the database is under heavy contention, the queue fills up and drops excess logs to safeguard request processing (with dropped-log metrics emitted). This guarantees that gateway request forwarding never stalls on database health.

---

## ADR-005: In-Memory Request Coalescing (Single-Flight) Combined with Redis Response Cache

- **Context**: High-traffic duplicate requests (e.g., identical prompts sent concurrently by multiple users or automated pipelines) can overwhelm upstream providers and cost money.
- **Decision**: Combine a distributed Redis exact-match cache with an in-memory single-flight coalescing mechanism using `asyncio.Event` keyed by request hash. When identical requests arrive concurrently on a single gateway instance, only the first request dispatches upstream; subsequent identical requests await the leader's completion and share the result.
- **Alternatives Considered**:
  - *Redis distributed locks for single-flight*: Incurs Redis round trips and lock acquisition latency for every incoming request.
  - *No single-flight, Redis cache only*: Multiple concurrent cache misses for the same prompt still hit upstream providers simultaneously (cache stampede).
- **Tradeoffs**: Single-flight coalescing is scoped to each gateway replica process rather than cross-replica, but this avoids distributed locking overhead while preventing local cache stampedes with minimal complexity.

---

## ADR-006: SQLite with WAL Mode as the Sole Database Engine

- **Context**: The gateway needs persistent storage for API keys and request logs. The project owner is a student who needs to understand and run the entire stack effortlessly, without managing a heavy external database daemon.
- **Decision**: Use SQLite via `aiosqlite` with Write-Ahead Logging (`PRAGMA journal_mode=WAL;`). All database access is concentrated in a single file `backend/src/relay/db.py` using plain parameterized SQL queries, without an ORM or migration framework.
- **Alternatives Considered**:
  - *PostgreSQL + SQLAlchemy + Alembic*: Enterprise-grade, but introduces high cognitive load, complex migration dependencies, network setup overhead, and slows down local setup.
  - *Pure in-memory dictionaries*: Non-persistent, losing keys and logs on restart.
- **Tradeoffs**: SQLite limits write concurrency to a single writer process at a time. It cannot be shared across multiple physical servers. However, combined with WAL mode and asynchronous batch writing, it easily handles hundreds of requests per second with sub-millisecond query latencies and zero operational burden.

---

## ADR-007: Next.js App Router for the Live Control Dashboard

- **Context**: The live dashboard must render real-time request logs, provider health cards, chaos injection controls, playground testing, usage charts, and key management.
- **Decision**: Build the frontend using Next.js 14+ (App Router, TypeScript, TailwindCSS) with native browser `EventSource` for real-time SSE streaming from the gateway's `/admin/events` endpoint, and plain `fetch` for REST administrative actions.
- **Alternatives Considered**:
  - *Vite + React SPA*: Lightweight, but Next.js App Router provides standard modern structure, fast builds, and clean file-based routing.
  - *Server-rendered templates (Jinja2 / HTMX)*: Good for simple CRUD, but less suited for high-frequency live dashboard charts and interactive playground state.
- **Tradeoffs**: Next.js requires Node.js tooling to build and run, but delivers a polished, responsive operator UI with typed client components.

---

## ADR-008: Exponentially Weighted Moving Average (EWMA) for Lowest-Latency Routing

- **Context**: The `lowest_latency` routing strategy needs to pick the fastest healthy provider target for each request, adapting quickly to provider degradation or recovery.
- **Decision**: Calculate provider latency using an EWMA stored in Redis: $EWMA_{t} = \alpha \cdot L_t + (1 - \alpha) \cdot EWMA_{t-1}$ with smoothing factor $\alpha = 0.2$.
- **Alternatives Considered**:
  - *Arithmetic mean over last N requests*: Requires maintaining a sliding window array in Redis, consuming more memory and reacting slowly to sudden latency spikes.
  - *Instantaneous latency of last request*: Highly volatile; a single transient blip would unnecessarily divert all subsequent traffic.
- **Tradeoffs**: EWMA requires tuning the smoothing factor $\alpha$, but maintains $O(1)$ constant storage in Redis while reacting swiftly to upstream latency shifts.

---

## ADR-009: Normalized Provider Adapters with Zero-Key Offline Mock Fallback

- **Context**: Relay must support multiple real-world upstream LLM providers (OpenAI, Anthropic Claude, Google Gemini) which differ radically in wire protocol (endpoints, headers, message structures, system prompt placements, stop reason strings, streaming SSE events). At the same time, Relay must run, test, and demo completely offline with zero API keys required.
- **Decision**:
  1. Build dedicated adapter classes (`OpenAIProvider`, `AnthropicProvider`, `GeminiProvider`) extending the common `Provider` interface.
  2. Map incoming OpenAI-standard requests to vendor-specific wire formats (e.g., Anthropic top-level `system` string and role filtering; Gemini `contents` and `systemInstruction`).
  3. Map vendor-specific responses and streaming SSE events back to uniform `ProviderResponse` and `ChatCompletionChunk` objects.
  4. Catch all transport (`httpx`) errors and normalize into `RelayProviderError` with unified `status_code` and `is_retryable` flag (handling vendor-specific status codes like Anthropic 529 Overloaded).
  5. If vendor API keys are not supplied in the environment, any request targeted to that provider automatically falls back to a simulated `MockProvider`, allowing full local verification without network calls or external credentials.
- **Alternatives Considered**:
  - *Using LiteLLM or LangChain as an adapter library*: Would add heavy dependencies, obscure the protocol translation, and violate the project rule to keep dependencies minimal and code easily understandable for CS students.
  - *Requiring real API keys to run*: Breaks offline portability and test execution in isolated CI environments.
- **Tradeoffs**: Requires maintaining custom translation code for OpenAI, Anthropic, and Gemini schemas, but yields full transparency, zero bloat, and guaranteed offline testability.

