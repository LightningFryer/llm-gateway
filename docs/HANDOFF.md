# Relay: Final-Year CS Student Project Guide & Codebase Handoff

Welcome to **Relay**! This document is written specifically for you as the project owner. It contains everything you need to **understand**, **run**, and **confidently explain** this project in software engineering interviews.

---

## 1. What is Relay in Plain English?

Modern AI applications rarely rely on a single LLM provider like OpenAI. Providers suffer outages, rate limits, and latency spikes, and pricing varies wildly.

**Relay is an intelligent reverse proxy (gateway) that sits between client applications and LLM providers.**
Instead of calling OpenAI or Claude directly, applications call Relay (`http://localhost:8000/v1/chat/completions`). Relay automatically:
1. **Routes traffic** across providers using smart strategies (`lowest_latency`, `lowest_cost`, `priority`, or `round_robin`).
2. **Caches exact responses** in Redis/memory so repeat questions return in under 10ms for free.
3. **Trips circuit breakers** when a provider goes down and transparently redirects traffic to backup providers with 0 user-facing errors.
4. **Enforces rate limits & monthly budgets** so runaway scripts don't rack up unexpected bills.
5. **Presents a live Next.js control dashboard** with a chaos panel that lets you kill a provider live in an interview and watch traffic fail over in real time.

---

## 2. Where the Code Lives (Small, Clean Modules)

The codebase is deliberately organized with a flat, readable structure:

| File / Directory | What It Does | Why You Should Care |
| :--- | :--- | :--- |
| `backend/src/relay/api/openai_compat.py` | The main `/v1/chat/completions` router | The hot path of the gateway: caching, rate limiting, and failover loop. |
| `backend/src/relay/circuit_breaker.py` | 3-state circuit breaker | Implements `CLOSED`, `OPEN`, `HALF_OPEN` state transitions using Redis sorted sets. |
| `backend/src/relay/ratelimit.py` | Token bucket & sliding window limiters | Pure Python distributed rate limiting using Redis pipelines without Lua scripts. |
| `backend/src/relay/cache.py` | SHA-256 exact match cache & single-flight | Combines Redis cache with `asyncio.Event` to prevent cache stampedes. |
| `backend/src/relay/router.py` | Routing strategies & target planning | Implements EWMA lowest-latency, lowest-cost, and weighted round-robin. |
| `backend/src/relay/db.py` | SQLite database layer (WAL mode) | Pure parameterized SQL (no heavy ORMs). Includes the async batch queue logger. |
| `backend/src/relay/providers/mock.py` | Controllable mock LLM provider | Allows the entire stack to run 100% offline with zero external API keys! |
| `backend/src/relay/providers/` | Real adapters (OpenAI, Anthropic, Gemini) | Maps vendor-specific REST schemas to Relay's OpenAI-compatible types. |
| `dashboard/src/app/page.tsx` | Next.js Live Control Dashboard | Real-time SSE stream (`EventSource`), provider health cards, and traffic charts. |
| `dashboard/src/components/ChaosDrawer.tsx` | The Chaos Injection Panel | The presenter panel to kill providers, inject latency, or trigger mid-stream errors. |
| `scripts/load_generator.py` | Async traffic simulator | Sends realistic traffic to test caching, failover, and rate limits. |
| `scripts/seed.py` | Database seeder | Creates initial demo keys and outputs `.env.local`. |

---

## 3. How to Run Everything Locally

You can run Relay completely offline without spending any money or configuring any real API keys.

### Option A: Using Docker Compose (Single Command)
```bash
docker compose up --build
```
- Dashboard: [http://localhost:3000](http://localhost:3000)
- Gateway API: [http://localhost:8000](http://localhost:8000)
- Metrics: [http://localhost:8000/metrics](http://localhost:8000/metrics)

### Option B: Bare-Metal (Python + Node.js)

1. **Activate Python Virtual Environment & Seed Keys**:
   ```bash
   .venv\Scripts\activate
   python scripts/seed.py
   ```
2. **Start the Relay Gateway**:
   ```bash
   $env:PYTHONPATH="backend/src"
   uvicorn relay.main:create_app --factory --host 127.0.0.1 --port 8000
   ```
3. **Start the Next.js Dashboard**:
   ```bash
   cd dashboard
   npm run start -p 3000
   ```
4. **Generate Background Traffic**:
   ```bash
   python scripts/load_generator.py --rps 5 --duration 0 --repeat-ratio 0.3
   ```

---

## 4. Key Engineering Concepts to Explain in Interviews

Whenever an interviewer asks you about the technical depth of this project, highlight these 4 architectural achievements:

1. **Pure Python Distributed Coordination (No Lua Scripts)**:
   - *Why*: We chose to coordinate Redis state using pure Python pipelines, sorted sets, and `WATCH/MULTI/EXEC` transactions instead of Lua scripts. This keeps 100% of the business logic type-checked by `mypy`, fully testable with `fakeredis`, and easily readable.
2. **The Two-Phase Streaming Failure Boundary**:
   - *Pre-first-token*: If an upstream provider drops the connection before sending tokens, the gateway trips the breaker and transparently fails over to a backup provider. The client never sees an error.
   - *Mid-stream*: Once tokens have begun flushing over SSE, transparent switching is impossible without context corruption. Relay flushes a clean `event: error` SSE event and terminates cleanly.
3. **Decoupled Asynchronous Audit Logging**:
   - Writing request logs to disk on the request hot path ruins p99 latency. Relay pushes audit rows onto an in-memory `asyncio.Queue` and flushes batches to SQLite using WAL mode (`PRAGMA journal_mode=WAL;`), ensuring the client response is never stalled on disk I/O.
4. **Single-Flight Request Coalescing**:
   - If 50 users send the exact same query simultaneously when the cache is cold, traditional systems send 50 requests upstream (cache stampede). Relay uses in-memory `asyncio.Event` synchronization so only the first request queries upstream while the remaining 49 await its completion and share the cached answer.

---

## 5. Helpful Documentation Map

- Detailed Architecture & Diagrams: `docs/ARCHITECTURE.md`
- 30-Second Chaos Demo Script: `docs/DEMO.md`
- Learning Guide & 21 Interview Q&As: `docs/LEARNING.md`
- Real Performance Benchmarks: `docs/BENCHMARKS.md`
- Architecture Decision Records (9 ADRs): `docs/DECISIONS.md`
- Full API Reference: `docs/API.md`

