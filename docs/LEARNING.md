# Relay: Learning Guide & Interview Preparation

This document explains the core distributed systems and backend engineering concepts implemented in Relay in plain English, details how Relay implements each concept, and provides 3 targeted interview questions and answers for each topic. It is designed for students, interview candidates, and engineers seeking to explain every architectural layer of an LLM gateway.

---

## 1. The Database Layer (SQLite with WAL Mode & Async Batching)

### Concept in Plain English
A relational database stores persistent records like API keys and request logs. In high-throughput backend services, writing to a database on the synchronous "hot path" of every user request adds network or disk latency and creates a bottleneck. SQLite is an embedded, in-process database that requires zero configuration or external servers. However, by default, standard SQLite locks the entire database file during writes, blocking readers. Enabling Write-Ahead Logging (WAL mode) allows readers and writers to operate concurrently without blocking each other.

### How Relay Implements It
- **Single File (`backend/src/relay/db.py`)**: All SQL queries, connection logic, and table creation live in one clear file using `aiosqlite`. There is no ORM or migration framework; schemas are created via plain `CREATE TABLE IF NOT EXISTS`.
- **WAL Mode**: On startup, Relay executes `PRAGMA journal_mode=WAL;` and `PRAGMA synchronous=NORMAL;`, allowing concurrent reads while writes append to the WAL file.
- **Off-the-Hot-Path Batch Queue**: Instead of awaiting a database insert before returning a response to the user, API handlers push log records into an in-memory `asyncio.Queue`. A dedicated background task drains the queue in batches (e.g. up to 100 items or every 500ms) and executes an `executemany` insert. If the queue is saturated, records are dropped and a metric is bumped, ensuring database slowdowns never degrade user response latency.

### Interview Questions & Answers
1. **Q: Why use SQLite for an LLM gateway, and what are its scaling limits?**  
   *A:* SQLite was chosen for architectural simplicity and zero-ops reliability: it requires no external database daemon, runs in-process with sub-millisecond query execution, and eliminates network hops. Its main limitation is that only one process can write to the SQLite file at a time; if you scale to multiple gateway containers on separate machines, a shared network disk like NFS can cause file locking issues. In a horizontally scaled multi-node production deployment, SQLite would be replaced with a distributed database like PostgreSQL.
2. **Q: What is WAL mode, and how does it prevent concurrency bottlenecks in SQLite?**  
   *A:* In traditional rollback journal mode, writing changes locks the entire database file, preventing readers from querying. In WAL (Write-Ahead Logging) mode, SQLite leaves the main database file untouched and appends new changes to a separate `-wal` file. Readers continue reading snapshot-isolated data from the main database and WAL simultaneously without blocking writes, significantly boosting concurrent read-heavy throughput.
3. **Q: Why batch write logs asynchronously via a queue instead of writing directly?**  
   *A:* Disk I/O is orders of magnitude slower than in-memory operations. Writing synchronously after every LLM completion would add 2-10ms of disk latency to every client request and would cause massive write lock contention under load. By enqueueing log payloads into an in-memory bounded `asyncio.Queue` and flushing in batches via `executemany`, we amortize disk transaction overhead across many requests and isolate client response times from database performance.

---

## 2. Circuit Breaker Pattern

### Concept in Plain English
When an external dependency (like an upstream LLM provider) is failing or timing out, repeatedly hammering it with requests wastes resources, exhausts connection pools, and inflates user latency. A circuit breaker acts like an electrical fuse: it monitors failure rates over a recent time window. If failures exceed a safety threshold, the breaker "trips" (opens), instantly failing or bypassing requests to that provider without making an expensive network call. After a cooldown period, it enters a "half-open" state to test the waters with a small number of probe requests. If the probes succeed, the circuit closes; if they fail, it reopens.

### How Relay Implements It
- **States**: `CLOSED` (healthy, all traffic allowed), `OPEN` (tripped, requests bypassed/diverted), `HALF_OPEN` (testing recovery with limited probes).
- **Shared State in Redis**: Circuit state and rolling failure timestamps are stored in Redis (`cb:{provider}:state` and `cb:{provider}:failures`), ensuring all gateway instances share identical circuit status.
- **Rolling Window**: Failures are logged with timestamps in a Redis sorted set. Only failures within the last $N$ seconds (e.g., 30s) are counted.
- **Trip Conditions**: If total requests in the window reach `min_requests` (e.g., 5) and the failure percentage exceeds `failure_threshold` (e.g., 50%), the breaker transitions to `OPEN`.
- **Event Bus Integration**: State transitions emit events (`circuit.opened`, `circuit.half_open`, `circuit.closed`) broadcast across Redis pub/sub to the live dashboard.

### Interview Questions & Answers
1. **Q: What problem does a Circuit Breaker solve in an LLM Gateway?**  
   *A:* Upstream LLM providers suffer outages, rate limits (429s), and network brownouts. Without a circuit breaker, every incoming request would hang for the full timeout (e.g., 30s) before failing or attempting a retry, exhausting the gateway's connection pool and degrading the entire system. A circuit breaker fails fast within 1ms when an upstream target is down, immediately routing requests to healthy fallback providers.
2. **Q: What is the difference between OPEN and HALF_OPEN state?**  
   *A:* In `OPEN` state, the circuit is fully tripped: no requests are sent upstream, and all calls are immediately diverted or rejected. In `HALF_OPEN` state (entered after a cooldown duration), the gateway cautiously permits a configured number of "probe" requests (e.g., 3) through to the upstream provider. If all probe requests succeed, the system considers the provider healthy and closes the circuit; if any probe fails, the circuit reopens immediately.
3. **Q: Why store circuit breaker state in Redis instead of process memory?**  
   *A:* In a production deployment running multiple gateway replicas, in-process circuit breakers would each need to independently experience enough failures before tripping. This causes "failure amplification", where every container hammers a failing provider. Storing circuit state in Redis ensures that when one container detects a failure cascade, all gateway replicas immediately respect the open circuit.

---

## 3. Retries and Exponential Backoff with Jitter

### Concept in Plain English
Transient errors (such as temporary network glitches or momentary 503 service overloads) often resolve themselves within milliseconds. Retrying immediately can fix the error. However, if hundreds of clients retry at the exact same instant, they create a "thundering herd" that overwhelms the recovering server. Exponential backoff increases the delay between successive retries ($t = base \times 2^{attempt}$). Adding "jitter" (random noise) scrambles the retry intervals across clients, spreading the traffic evenly over time.

### How Relay Implements It
- **Retryable Errors**: Only transient errors are retried: HTTP 500, 502, 503, 504, 429 (rate limits), and connection timeouts. Client errors (400, 401, 403, 404, 422) are never retried because retrying invalid inputs will always yield the same failure.
- **Full Jitter Calculation**: `sleep = random.uniform(0, min(max_delay, base_delay * (2 ** attempt)))`.
- **Respecting `Retry-After`**: If an upstream provider returns a 429 with a `Retry-After` header, Relay uses that explicit duration instead of generic backoff.

### Interview Questions & Answers
1. **Q: Why is "Full Jitter" preferred over deterministic exponential backoff?**  
   *A:* Pure exponential backoff ensures delays increase, but concurrent requests that fail at time $T$ will still all retry simultaneously at $T + 2^k$. This produces repeated bursts of synchronized traffic (thundering herd problem). Full jitter draws the sleep duration uniformly at random between 0 and the exponential cap, effectively smoothing the retry traffic into a continuous, manageable stream.
2. **Q: Why should client 4xx errors (like 401 or 422) never be retried?**  
   *A:* 4xx errors represent client-side faults (e.g., invalid API keys, malformed JSON schemas, missing parameters). Unlike a transient 503 or network timeout, sending the exact same payload with the exact same credentials will deterministically produce the exact same error, wasting gateway resources and delaying error feedback to the user.
3. **Q: How does the gateway handle an upstream 429 with a `Retry-After` header?**  
   *A:* Upstream providers explicitly communicate when capacity will be restored via the `Retry-After` header (in seconds or an HTTP date). Rather than guessing with exponential backoff, Relay parses the header and pauses for the requested duration (bounded by a maximum allowable wait limit) before making the retry attempt.

---

## 4. Rate Limiting: Token Bucket vs. Sliding Window Log

### Concept in Plain English
Rate limiting protects APIs from abuse, runaway loops, and denial-of-service by restricting how many requests or tokens a client can consume within a given time period.
- **Token Bucket**: Imagine a bucket that holds tokens up to a maximum capacity. Tokens are added at a constant rate per second. Each request consumes one (or more) tokens. If the bucket is empty, the request is rejected. This algorithm allows controlled bursts (up to bucket capacity) while maintaining a strict long-term average rate.
- **Sliding Window Log**: Stores a timestamp for every request in a sorted log. When a new request arrives, all timestamps older than the window (e.g., 60 seconds) are pruned. If the number of remaining timestamps is under the limit, the new timestamp is added and the request is permitted. This provides mathematically exact rate limiting with zero burst vulnerability at window boundaries.

### How Relay Implements It
- **No Lua Scripts**: All Redis operations use standard `redis-py` commands.
- **Token Bucket (`ratelimit.py`)**: Stores `tokens` and `last_updated` in a Redis Hash. It uses optimistic concurrency control (`WATCH`, read, compute refilled tokens, `MULTI`, `HSET`, `EXEC`) with bounded retries on `WatchError`.
- **Sliding Window Log (`ratelimit.py`)**: Uses a Redis Sorted Set (`ZSET`) where the score and member are the request timestamp. Executed in a pipeline: `ZREMRANGEBYSCORE(now - window)`, `ZADD(now)`, `ZCARD`, `EXPIRE`. If `ZCARD` > limit, the entry is rolled back with `ZREM` and the request is rejected with HTTP 429.
- **Dual Tracking**: Both Requests Per Minute (RPM) and Tokens Per Minute (TPM) are enforced. Estimated tokens (via `tiktoken`) are checked upfront, then reconciled after the actual upstream completion.

### Interview Questions & Answers
1. **Q: How does the boundary burst problem affect fixed window counters, and how does sliding window log solve it?**  
   *A:* In a fixed 1-minute window counter (e.g. 12:00 to 12:01), an attacker could send 100 requests at 12:00:59 and another 100 requests at 12:01:01. Both individual minute windows pass the 100 req/min limit, but 200 requests were processed within a 2-second span. Sliding window log stores exact timestamps and prunes entries older than $(now - 60s)$, ensuring that any rolling 60-second window strictly enforces the limit.
2. **Q: Compare the space and time complexity of Token Bucket vs. Sliding Window Log.**  
   *A:* Token Bucket requires $O(1)$ memory per key (storing just two numbers: token count and last timestamp) and $O(1)$ time complexity. Sliding Window Log requires $O(N)$ memory where $N$ is the number of requests in the window, and $O(\log N)$ time for sorted set insertions and removals. Token bucket is far more memory efficient for high-volume endpoints, while sliding window log guarantees exact precision without bursts.
3. **Q: How do you achieve atomic operations in Redis without Lua scripts?**  
   *A:* Using Redis transactions (`MULTI/EXEC`) and optimistic locking with `WATCH`. By executing `WATCH key`, Redis monitors the key for changes made by other clients. The client reads the current state, computes the new state in Python, starts `MULTI`, queues the write commands, and executes `EXEC`. If another client modified the key in the interim, `EXEC` aborts with a `WatchError`, prompting the application to retry the loop.

---

## 5. Response Caching and Request Coalescing (Single-Flight)

### Concept in Plain English
LLM inference is expensive and slow (often 1-5 seconds per request). If identical prompts are sent repeatedly (e.g. deterministic extraction with temperature 0), returning a cached response reduces latency from seconds to under 5 milliseconds and costs $0.
**Request Coalescing (Single-Flight)** addresses a related problem: what happens when 50 identical requests arrive *at the exact same second* before anything is cached? Without coalescing, all 50 requests miss the cache and hit the upstream LLM simultaneously (a cache stampede). Request coalescing ensures only the first request actually calls the LLM; the other 49 wait in memory for the first request's result.

### How Relay Implements It
- **Deterministic Cache Key**: SHA-256 hash of the normalized request parameters: model alias, message list (roles and content), and deterministic sampling parameters (`temperature`, `max_tokens`, `top_p`, `stop`).
- **Safety Rules**: Only deterministic requests (`temperature == 0.0` by default) are cached. Requests with `Cache-Control: no-cache` bypass the cache.
- **Single-Flight Coalescing**: Uses an in-memory dictionary of `asyncio.Event` objects keyed by the request hash. When a request arrives:
  1. Check Redis cache. If hit, return immediately.
  2. If an in-flight event exists for this hash, await the event and then read the cached result.
  3. If no event exists, create one, make the upstream call, write to Redis cache, set the event, and remove the entry from the in-flight dictionary.
- **Streaming Cache Replay**: When a cached entry is requested via `stream=True`, Relay breaks the stored text into realistic SSE token chunks and yields them with a 15ms pacing delay, preserving expected client streaming behavior.

### Interview Questions & Answers
1. **Q: Why should LLM caching only occur when temperature is 0?**  
   *A:* Temperature controls the randomness of token sampling during LLM generation. When `temperature > 0`, the caller explicitly expects diverse, non-deterministic responses on subsequent requests. Caching non-zero temperature completions violates this expectation by returning identical outputs. At `temperature == 0`, token selection is greedy and deterministic, making it safe to cache.
2. **Q: What is a "cache stampede" and how does single-flight coalescing prevent it?**  
   *A:* A cache stampede occurs when a cache item expires or is queried concurrently by many clients before it can be populated. All incoming requests experience a cache miss at once and concurrently execute the expensive upstream operation, causing latency spikes and severe upstream load. Single-flight coalescing ensures that only one worker executes the upstream call while identical concurrent callers await its resolution in memory.
3. **Q: How does Relay stream a response that was retrieved from cache?**  
   *A:* If a client requests `stream=True` but the result hits the cache, returning a single monolithic JSON response would break clients expecting an SSE stream of `data: {...}` chunks. Relay reconstructs OpenAI-compatible streaming chunks, tokenizes or slices the cached completion text, and yields SSE chunks asynchronously with a small sleep delay, ensuring seamless client compatibility.

---

## 6. Routing Strategies & Dynamic Health Filtering

### Concept in Plain English
In a multi-provider gateway, an alias like `smart` might map to multiple models across different providers (e.g. OpenAI GPT-4o, Anthropic Claude 3.5 Sonnet, Gemini 1.5 Pro). The router decides which provider receives each request based on business objectives:
- `priority`: Fallback chain (always try provider 1 first; if unavailable, try provider 2).
- `round_robin`: Distributes requests evenly or according to configured weights.
- `lowest_latency`: Continuously routes to whichever provider has the lowest recent response latency using an Exponentially Weighted Moving Average (EWMA).
- `lowest_cost`: Routes to the provider with the lowest price per 1M tokens that is currently healthy.

### How Relay Implements It
- **Health Filtering**: Before applying any strategy, the router checks the Circuit Breaker state of each target. Any target whose circuit is `OPEN` is filtered out.
- **EWMA Latency Tracking**: Whenever a request finishes, the provider's latency updates a shared key in Redis: $EWMA_{new} = \alpha \times latency + (1 - \alpha) \times EWMA_{old}$.
- **Fallback Chain on Failure**: If the selected candidate fails upstream, the router moves to the next candidate in the alias target list.

### Interview Questions & Answers
1. **Q: Why use Exponentially Weighted Moving Average (EWMA) instead of a simple arithmetic mean for latency routing?**  
   *A:* A simple arithmetic mean over all historical requests is heavily weighed down by past performance and reacts too slowly to sudden provider slowdowns. EWMA applies exponentially decreasing weights to older data points, prioritizing recent requests. If an upstream provider suddenly experiences latency spikes, its EWMA rises immediately, diverting traffic within seconds.
2. **Q: How do routing strategies interact with the Circuit Breaker?**  
   *A:* The Circuit Breaker acts as an upstream gatekeeper for the router. Before the routing strategy evaluates candidate targets (whether by priority, latency, or cost), it queries the circuit status of each provider. Targets in the `OPEN` state are excluded from the candidate pool, preventing the gateway from routing traffic to known-failing backends.
3. **Q: What happens if all configured targets for an alias have open circuits?**  
   *A:* If all targets are open or failing, the gateway returns an HTTP 503 Service Unavailable with an OpenAI-compatible JSON error body and a `Retry-After` header, communicating to the client that upstream providers are temporarily exhausted.

---

## 7. Server-Sent Events (SSE) & Streaming Resilience

### Concept in Plain English
Server-Sent Events (SSE) is an HTTP standard that allows a server to push real-time events to a client over a persistent connection using plain text formatted as `data: {...}\n\n`. In LLM applications, streaming yields tokens as they are generated, drastically reducing the perceived Time-To-First-Token (TTFT) from seconds to milliseconds.

### How Relay Implements It
- **Two-Phase Failure Handling**:
  1. *Pre-first-token failure*: If the upstream provider fails before yielding the first chunk, the gateway catches the exception, updates the circuit breaker, and cleanly falls over to the next provider. The client experiences zero disruption except a slight delay.
  2. *Mid-stream failure*: If the provider drops connection after the client has already received tokens, transparent failover is impossible (merging outputs from different models mid-sentence produces corrupted outputs). Relay emits a formatted terminal SSE error event (`event: error`), terminates the stream with `data: [DONE]`, and records the failure against the circuit breaker.
- **Client Disconnection Handling**: If the end user closes their browser or aborts the HTTP request, Relay's async event loop detects the broken connection and immediately cancels the upstream `httpx` streaming request, preventing wasted token costs.
- **Admin Event Stream**: `/admin/events` broadcasts gateway events (`request.completed`, `circuit.opened`, `chaos.changed`) using Redis Pub/Sub, allowing multiple dashboard instances to display live system state.

### Interview Questions & Answers
1. **Q: Why can't an LLM gateway fail over to a second provider mid-stream?**  
   *A:* Once tokens have been sent over the HTTP response stream to the client, the client has already rendered them. Different LLM providers use different tokenizers, internal prompt representations, and writing styles. Switching providers mid-stream would require either asking the second provider to continue from an arbitrary partial token context (which is unreliable and may repeat words or change tone) or restarting the entire response, corrupting the stream for the client.
2. **Q: How does the gateway prevent wasting money when a client disconnects during a 2,000-token stream?**  
   *A:* In async Python with FastAPI and `httpx`, the response generator checks client connection status on each iteration. If the client disconnects, an `asyncio.CancelledError` is raised. Relay catches this cancellation and calls `response.aclose()` on the upstream `httpx` stream, terminating the upstream connection and halting token generation at the provider.
3. **Q: Why use Server-Sent Events (SSE) instead of WebSockets for LLM streaming and the dashboard?**  
   *A:* LLM token streaming and dashboard metrics are unidirectional: the server streams data to the client, but the client does not need to send bidirectional binary frames back over the same channel. SSE operates over standard HTTP/1.1 or HTTP/2, works natively with browser `EventSource`, traverses proxies and corporate firewalls without special configuration, and features built-in reconnection protocols.

