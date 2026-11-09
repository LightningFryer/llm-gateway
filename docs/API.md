# Relay API Reference Specification

Relay provides two distinct API surfaces:
1. **OpenAI-Compatible API (`/v1/*`)**: Drop-in compatible with standard OpenAI SDKs (`openai.OpenAI`, `openai.AsyncOpenAI`, LangChain, LlamaIndex, etc.).
2. **Administrative Control & Chaos API (`/admin/*`)**: Management endpoints for provider states, chaos injection, API key CRUD, routing configurations, and live SSE event feeds.

---

## 1. Authentication

- **Client Requests (`/v1/*`)**: Require a valid bearer token in the `Authorization` header:
  ```http
  Authorization: Bearer rl_live_uMnCgvAClu2FEqGfc14x2UKb9dATx3vB
  ```
- **Admin Requests (`/admin/*`)**: Require the configured admin secret:
  ```http
  Authorization: Bearer relay_admin_secret_key_12345
  ```

---

## 2. OpenAI-Compatible Endpoints (`/v1`)

### `POST /v1/chat/completions`

Creates a model response for the given chat conversation.

#### Request Headers
- `Authorization: Bearer <API_KEY>` (Required)
- `Content-Type: application/json` (Required)
- `Cache-Control: no-cache` (Optional: bypasses response cache)

#### Request Body
```json
{
  "model": "smart",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Explain circuit breaking."}
  ],
  "temperature": 0.0,
  "max_tokens": 500,
  "stream": false
}
```

#### Non-Streaming Response (`stream: false`)
```json
{
  "id": "chatcmpl-a1b2c3d4e5f6",
  "object": "chat.completion",
  "created": 1700000000,
  "model": "smart",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "A circuit breaker trips to open during upstream failures..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 15,
    "completion_tokens": 24,
    "total_tokens": 39
  }
}
```

#### Response Headers Returned
- `X-Relay-Request-Id`: Unique request trace identifier
- `X-Relay-Provider`: Provider that answered the request (e.g. `mock-a`, `mock-b`, `cache`)
- `X-Relay-Cache`: `HIT` or `MISS`
- `X-Relay-Attempts`: Number of provider targets attempted
- `X-Relay-Latency-Ms`: Execution duration in milliseconds

#### Streaming Response (`stream: true`)
Returns standard Server-Sent Events (SSE):
```http
HTTP/1.1 200 OK
Content-Type: text/event-stream
Cache-Control: no-cache

data: {"id":"chatcmpl-123","object":"chat.completion.chunk","model":"smart","choices":[{"index":0,"delta":{"role":"assistant","content":"A"}}]}

data: {"id":"chatcmpl-123","object":"chat.completion.chunk","model":"smart","choices":[{"index":0,"delta":{"content":" circuit"}}]}

data: {"id":"chatcmpl-123","object":"chat.completion.chunk","model":"smart","choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}

data: [DONE]
```

---

### `GET /v1/models`

Lists all configured model aliases and strategies.

#### Response
```json
{
  "object": "list",
  "data": [
    {
      "id": "fast",
      "object": "model",
      "created": 1700000000,
      "owned_by": "relay",
      "description": "Low-latency target chain optimized for fast completions",
      "strategy": "lowest_latency",
      "targets_count": 2
    },
    {
      "id": "smart",
      "object": "model",
      "created": 1700000000,
      "owned_by": "relay",
      "description": "High-reasoning target chain with priority fallback",
      "strategy": "priority",
      "targets_count": 3
    }
  ]
}
```

---

## 3. Admin & Chaos Endpoints (`/admin`)

### `GET /admin/providers`
Returns health, circuit state, and current chaos knobs for all registered providers.

### `POST /admin/chaos`
Dynamically modifies chaos parameters at runtime.
```json
{
  "provider": "mock-a",
  "is_down": true,
  "base_latency_ms": 250,
  "error_rate": 0.25,
  "fail_after_tokens": 3
}
```

### `POST /admin/chaos/reset`
Restores all providers back to zero latency, zero errors, and healthy status.

### `GET /admin/keys`
Lists all generated client API keys with prefix, rate limits, and budget spend.

### `POST /admin/keys`
Creates a new client API key. Returns raw secret (shown only once) and key record. Accepts `rpm_limit` (or `rate_limit_rpm`) and `tpm_limit` (or `rate_limit_tpm`).
```json
{
  "name": "staging-client",
  "rpm_limit": 120,
  "tpm_limit": 200000,
  "monthly_budget_usd": 100.0
}
```

### `POST /admin/keys/{id}/rotate`
Rotates the secret of an existing key. Invalidates the old secret immediately.

### `PATCH /admin/keys/{id}`
Enables or disables an API key (`{"enabled": false}` or `{"is_active": false}`).

### `GET /admin/config`
Returns parsed contents of `config/models.yaml`.

### `POST /admin/config/reload`
Hot-reloads `config/models.yaml` from disk without server downtime.

### `GET /admin/stats`
Returns aggregated 24h metrics: total requests, spend, cache hit rate, p95 latency, and top keys.

### `GET /admin/events`
Live Server-Sent Events stream emitting real-time gateway notifications:
- `request_completed`: Request details (latency, tokens, cost, cache status)
- `failover`: Emitted when an upstream target fails and traffic falls over
- `circuit_tripped`: Emitted when a circuit opens
- `circuit_recovered`: Emitted when a circuit closes

---

## 4. Health & Observability Endpoints

- `GET /healthz`: Basic liveness probe (`{"status":"ok","service":"relay"}`).
- `GET /readyz`: Readiness probe checking SQLite database connectivity. Returns HTTP 503 if unhealthy.
- `GET /metrics`: Standard Prometheus metrics exposition scraping target.

