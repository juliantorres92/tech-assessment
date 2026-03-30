# Section C – Demo Service and Reliability Testing

---

## Overview

The demo service simulates a **Policy Issuance Process API** that calls a **Salesforce System API** using the Section B integration framework. The upstream system is intentionally unstable — it simulates the failure conditions a real insurance integration faces in production.

The demo requires no external dependencies and runs with a single command.

---

## How to Run

**Requirements:** Python 3.8+

```bash
# From the repository root
python src/demo.py
```

No additional packages needed. The unstable upstream server runs in the same process on port 9999.

---

## What the Demo Shows

### Architecture

```
Demo Service (Policy Issuance API)
    │
    ├── IntegrationClient (framework.py)
    │       ├── Idempotency check
    │       ├── Circuit breaker check
    │       ├── HTTP call with timeout (5s)
    │       ├── Retry + exponential backoff + jitter
    │       └── Structured logging + trace propagation
    │
    └──> Unstable Upstream (simulated Salesforce System API :9999)
              ├── 30% success (HTTP 200)
              ├── 60% transient failure (HTTP 503)
              └── 10% timeout (response after 8s > client timeout 5s)
```

---

## Failure Scenarios Demonstrated

### Scenario 1 — Retry under Transient Failures

The upstream returns HTTP 503 randomly. The framework retries up to 3 times with exponential backoff + jitter before giving up.

**Example console output:**
```json
{"level": "warning", "event": "call_failed_retrying",
 "url": "http://localhost:9999/v1/policies",
 "attempt": 0, "error": "HTTP 503 from ...", "wait_seconds": 0.52, "trace_id": "4bf92f..."}

{"level": "warning", "event": "call_failed_retrying",
 "url": "http://localhost:9999/v1/policies",
 "attempt": 1, "error": "HTTP 503 from ...", "wait_seconds": 1.18, "trace_id": "4bf92f..."}

{"level": "info", "event": "call_succeeded",
 "url": "http://localhost:9999/v1/policies", "attempt": 2, "trace_id": "4bf92f..."}
```

**How resilience mitigates the problem:** The client retries automatically. The calling service receives a successful response without knowing about the underlying failures. Progressive backoff gives the upstream time to recover between attempts.

---

### Scenario 2 — Per-Request Timeout

The upstream sleeps for 8 seconds. The client applies a 5-second timeout, fails fast, and retries.

**How resilience mitigates the problem:** Without a timeout, the client thread would be blocked for 8+ seconds — violating the calling service's SLA (e.g., a web user waiting for a quote). The timeout forces a fast fail, allowing the retry to potentially reach a healthy upstream worker.

---

### Scenario 3 — Circuit Breaker Opens

After 3 consecutive failures (demo threshold), the circuit opens. Subsequent calls are rejected immediately without touching the upstream.

**Example console output:**
```json
{"level": "warning", "event": "circuit_breaker_opened",
 "circuit": "salesforce-system-api",
 "failure_count": 3, "recovery_seconds": 10.0}

{"level": "warning", "event": "circuit_breaker_rejected",
 "url": "http://localhost:9999/v1/policies",
 "circuit": "salesforce-system-api", "trace_id": "7c3a1f..."}
```

**How resilience mitigates the problem:** The circuit breaker stops hitting the already-failing upstream, giving it space to recover. The caller receives a fast, predictable `CircuitBreakerOpenError` — instead of waiting through multiple timeouts and retries.

---

### Scenario 4 — Idempotency Cache Hit

A request with a previously successful idempotency key is resubmitted. The framework returns the cached response without making any network call.

**Example console output:**
```json
{"level": "info", "event": "idempotency_cache_hit",
 "idempotency_key": "demo-policy-001", "trace_id": "4bf92f..."}
```

**How resilience mitigates the problem:** In production, a mobile client might retry a policy issuance request after a network drop. Without idempotency, Salesforce would create a duplicate policy. The idempotency key guarantees the operation executes exactly once — even across multiple retries.

---

## Observability Output

Each event produces a structured JSON log entry visible in the console. In production, these entries flow to the centralized log aggregation platform. The `trace_id` field links all log entries from the same request chain — across retries, services, and synchronous/asynchronous boundaries.

---

## Production Equivalents

| Demo Component | Production Equivalent |
|---|---|
| In-process unstable HTTP server | Salesforce System API under degradation |
| In-memory idempotency store | Redis distributed cache with TTL |
| Trace context with `print()` | OpenTelemetry SDK → Collector → Jaeger/Datadog |
| Single Python process | MuleSoft CloudHub worker with multiple replicas |
| `CircuitBreakerOpenError` | MuleSoft error handler returning HTTP 503 + Retry-After header |
