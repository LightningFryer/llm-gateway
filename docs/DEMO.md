# Relay: The 30-Second Live Chaos Demo Script

This script walks through the **flagship presentation moment** that proves Relay's distributed resilience, automatic circuit breaking, and real-time observability in under 30 seconds.

---

## Prerequisites (10-Second Setup)

1. Start the stack (Backend & Next.js Dashboard):
   ```bash
   # Terminal 1: Gateway
   uvicorn relay.main:create_app --factory --host 127.0.0.1 --port 8000

   # Terminal 2: Dashboard
   cd dashboard && npm run start -p 3000
   ```
2. Open the dashboard in your browser: [http://localhost:3000](http://localhost:3000).
3. Start continuous background traffic in another terminal:
   ```bash
   python scripts/load_generator.py --rps 5 --duration 0 --repeat-ratio 0.3
   ```

---

## The 30-Second Demo Script

### Step 1: Show Traffic Flowing Normally (0 to 10 seconds)
- **Presenter says:**
  > *"Here is Relay's live control dashboard. Continuous user traffic is entering the gateway, and requests are being load-balanced across our upstream providers—`mock-a`, `mock-b`, and `mock-c`. The live feed at the bottom shows request latencies around 150ms and exact-match cache hits returning in under 10ms."*
- **Visual evidence on screen:**
  - Status indicator in the top navbar shows **Gateway Online** with a pulsing green dot.
  - Upstream provider cards for `mock-a`, `mock-b`, and `mock-c` all display **Circuit: CLOSED** in green.
  - The live request table is actively scrolling with incoming requests showing HTTP `200 OK`.

---

### Step 2: Kill Provider A (10 to 20 seconds)
- **Presenter action:**
  - Click the **"Kill Provider"** button directly on the `mock-a` health card (or open the **Chaos Panel** in the top right and toggle `mock-a` to FORCED DOWN).
- **Presenter says:**
  > *"Now watch what happens when our primary provider experiences an outage. I just clicked 'Kill Provider A'. Notice how `mock-a` immediately turns red. The gateway detected the connection drops, tripped `mock-a`'s circuit breaker to OPEN, and automatically shifted 100% of incoming traffic to `mock-b` and `mock-c`."*
- **Visual evidence on screen:**
  - `mock-a`'s card turns red, displaying **Circuit: OPEN** and **Status: Forced Down**.
  - Traffic in the live request feed continues streaming with **zero user-facing errors (0% error rate)**.
  - The "Provider" column in the live stream switches from `mock-a` to `mock-b`.
  - The Live Traffic Share chart shows `mock-a` dropping to 0% and `mock-b` taking over.

---

### Step 3: Restore Provider A (20 to 30 seconds)
- **Presenter action:**
  - Click **"Restore Provider"** on `mock-a`.
- **Presenter says:**
  > *"Now let's recover the provider. I click 'Restore Provider'. `mock-a`'s circuit breaker enters HALF_OPEN state to send a small number of test probe requests. Once the probes succeed, the circuit transitions back to CLOSED, and traffic immediately rebalances across the provider pool."*
- **Visual evidence on screen:**
  - `mock-a` badge transitions from **OPEN (red)** to **HALF_OPEN (amber)** to **CLOSED (green)**.
  - Traffic immediately re-engages with `mock-a` in the Live Traffic Share chart.
  - The total error rate remains at **0.0%**.

---

## Common Interview Follow-Up Questions

1. **"Why didn't any clients see an error when Provider A died?"**
   > *"Because Relay implements a pre-first-token failure boundary. When Provider A failed before sending any tokens over the wire, the gateway caught the connection error, recorded a failure in Provider A's circuit breaker, and immediately fell over to Provider B. From the client's perspective, the request simply took ~15ms longer."*

2. **"What happens if Provider A fails halfway through streaming tokens?"**
   > *"Once tokens have already been streamed to the user's screen over SSE, transparent switching is impossible because different models produce disjoint sentences and token alignments. Relay flushes a standardized `event: error` SSE event, closes the stream cleanly, trips the breaker, and logs the incident."*

3. **"How does the gateway prevent cache stampedes when a popular query arrives simultaneously?"**
   > *"Relay uses single-flight request coalescing with in-memory `asyncio.Event` synchronization keyed by the SHA-256 prompt hash. The first request becomes the leader and queries upstream; all concurrent peers await the leader's event and share the result from cache, ensuring only one request ever hits upstream."*

