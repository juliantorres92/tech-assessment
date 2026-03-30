# Section B – Reusable Integration Framework

---

## Overview

The integration framework is a reusable Python module ([`src/framework.py`](../src/framework.py)) that provides a single entry point for all outbound HTTP calls from a service. Every integration goes through this client — resilience patterns are applied once here, without duplicating them at each integration point.

---

## Components

### 1. Centralized Configuration (`IntegrationConfig`)

All tunable parameters are declared in a single dataclass. In production, these values are loaded from environment variables or a configuration service (MuleSoft properties, AWS Parameter Store, etc.).

```python
config = IntegrationConfig(
    max_retries=3,
    base_backoff_seconds=0.5,
    max_backoff_seconds=10.0,
    jitter_factor=0.3,
    timeout_seconds=5.0,
    circuit_breaker_failure_threshold=5,
    circuit_breaker_recovery_seconds=30.0,
    service_name="policy-issuance-api"
)
```

**Design decision:** Centralizing configuration avoids magic numbers scattered across the code and allows runtime tuning without code changes.

---

### 2. Retry with Exponential Backoff and Jitter

**Formula:** `wait = min(cap, base × 2^attempt) + random_jitter`

| Attempt | Base delay | With cap | + Jitter (30%) | Approx. wait |
|---|---|---|---|---|
| 0 (1st retry) | 0.5s | 0.5s | ±0.15s | ~0.5–0.65s |
| 1 (2nd retry) | 1.0s | 1.0s | ±0.30s | ~1.0–1.30s |
| 2 (3rd retry) | 2.0s | 2.0s | ±0.60s | ~2.0–2.60s |

**Why exponential backoff:** Progressive delays give the downstream system increasing time to recover between attempts. A fixed retry interval can overload a partially recovering backend.

**Why jitter:** When multiple clients fail simultaneously (e.g., a brief network outage), they all retry at the same interval without jitter — creating a simultaneous retry avalanche that prevents the recovering backend from stabilizing. Jitter spreads retries over time, smoothing the load.

**Why 3 retries:** Based on observed transient failure windows — most transient errors resolve within 2 retries. A 4th retry adds latency without proportional benefit and may compromise the calling service's SLO.

---

### 3. Circuit Breaker

Three-state machine that protects downstream systems from overload during failure windows:

```
CLOSED ──(failure threshold)──> OPEN ──(recovery window)──> HALF-OPEN
   ^                                                               |
   └──────────────────(successful probe call)─────────────────────┘
```

| State | Behavior |
|---|---|
| **Closed** | Normal operation. Failures are counted. |
| **Open** | All calls are rejected immediately (fast fail). The downstream system rests. |
| **Half-Open** | One probe call is allowed. Success → Closed. Failure → Open. |

**Why the circuit breaker matters:** Without it, retry storms from multiple clients hit a failing downstream system — preventing it from recovering. The circuit breaker breaks this cycle by stopping calls entirely during the recovery window.

**Configuration:** 5 failures → Open; 30s recovery window before Half-Open probe call.

---

### 4. Per-Request Timeout

Every HTTP call is bounded by a configurable timeout (default: 5 seconds). This prevents a slow downstream system from blocking a thread indefinitely — critical in user-facing synchronous flows where the total response time budget is bounded by the calling service's SLA.

**Design decision:** The timeout is always configured lower than the calling service's own timeout, to ensure the framework can handle the failure gracefully before the caller's deadline expires.

---

### 5. Idempotency Key Support

Mutating operations (POST, PUT, PATCH) accept an idempotency key. The framework stores successful responses indexed by that value. Duplicate requests return the cached response immediately without making a network call.

```python
response = client.call(
    url="https://salesforce-api/policies",
    method="POST",
    body={"account_id": "001...", "product": "life"},
    idempotency_key="policy-issuance-req-abc123"
)
```

**Why idempotency is essential for retry:** Without it, a request that timed out but was already processed by the downstream system (e.g., Salesforce created the policy) would generate a duplicate record on retry. The idempotency key makes retries safe.

**Production implementation:** The store would be a distributed cache (Redis) shared across all MuleSoft workers, with a TTL matching the maximum expected retry window.

---

### 6. Unified Structured Logging

Each log entry is a JSON object with a consistent schema:

```json
{
  "level": "warning",
  "event": "call_failed_retrying",
  "timestamp": "2026-03-28T19:00:00Z",
  "url": "https://salesforce-api/policies",
  "method": "POST",
  "attempt": 1,
  "error": "HTTP 503 from ...: Service Unavailable",
  "wait_seconds": 1.23,
  "trace_id": "4bf92f3577b34da6a3ce929d0e0e4736"
}
```

**Why structured logging:** Free-text logs cannot be reliably analyzed or queried in log aggregation platforms (Splunk, ELK). A fixed JSON schema allows filtering by `trace_id`, `idempotency_key`, or `event` type across millions of entries — essential for incident diagnosis.

---

### 7. OpenTelemetry Trace Propagation

Each outbound request carries trace headers (`traceparent`, `tracestate`) following the W3C Trace Context standard — the universal format for propagating trace IDs between services. This allows the observability platform to join the full call chain — from the digital channel through MuleSoft Process APIs to Salesforce and back — even across async messaging boundaries.

```
traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
tracestate:  sura=00f067aa0ba902b7
```

**In production:** Replace the lightweight `TraceContext` class with the `opentelemetry-sdk` package. The framework creates a record for each outbound call (span), captures status and latency, and exports them to the OpenTelemetry Collector (which feeds visualization tools like Jaeger or Datadog).

---

## Usage Example

```python
from framework import IntegrationClient, IntegrationConfig, TraceContext

config = IntegrationConfig(
    max_retries=3,
    timeout_seconds=5.0,
    circuit_breaker_failure_threshold=5,
    service_name="policy-issuance-api"
)

client = IntegrationClient(config=config, circuit_name="salesforce-system-api")
trace = TraceContext()  # Root span for this request

response = client.call(
    url="https://salesforce-system-api/policies",
    method="POST",
    body={"account_id": "001ABC", "product_code": "LIFE_CO"},
    idempotency_key="issuance-2026-03-28-001ABC-LIFE",
    trace_context=trace
)
```

---

## Design Decisions Summary

| Decision | Rationale |
|---|---|
| Single entry point `IntegrationClient` | Resilience patterns applied once, not duplicated per integration |
| Exponential backoff with jitter | Avoids simultaneous retry avalanche; gives downstream system progressive recovery time |
| Circuit breaker separate from retry | Retry handles transient errors; circuit breaker handles sustained outages — distinct failure modes require distinct responses |
| Idempotency at framework level | Safe retries for all mutating operations without the caller managing it |
| Structured JSON logging | Machine-readable logs for reliable queries in aggregation platforms |
| W3C standard trace headers | Universal propagation format; compatible with all major observability platforms |
| In-memory idempotency store (demo) | Production equivalent: Redis with TTL; same interface, distributed backing |
