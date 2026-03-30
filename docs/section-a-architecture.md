# Section A – Architecture and Roadmap

---

## 1. Target End-to-End Architecture — Multi-Country Digital Direct Channel

### Overview

The target architecture is designed for a multi-country insurance Digital Direct Channel, where Salesforce acts as the central system of record and MuleSoft Anypoint Platform as the universal integration middleware. Every inbound request from digital channels and every outbound request to external systems flows through the MuleSoft layer.

The architecture is built around four non-functional pillars:

- **High Availability:** No single point of failure; active-active deployment across multiple availability zones
- **Scalability:** Horizontal scaling at the MuleSoft runtime layer; Salesforce governor limits managed through bulkhead isolation
- **Resilience:** Retry with exponential backoff, circuit breaker, and bulkhead applied at the integration layer
- **Observability:** Unified logging, distributed traces (OpenTelemetry), and real-time dashboards across all integration flows

---

### Architecture Layers

#### Layer 1 — Digital Channels (Consumers)

- Web portal (customer self-service)
- Mobile application
- Agent and broker portal
- External partners (APIs)

All channels communicate exclusively through MuleSoft Experience APIs over HTTPS/REST. No channel has direct access to Salesforce or backend systems.

#### Layer 2 — API-Led Integration with MuleSoft (Middleware)

Three-layer API-led connectivity model:

**Experience APIs**
- Channel-specific adapters: one per channel type (web, mobile, broker, partner)
- Handles authentication (OAuth 2.0 / Connected App), request validation, and response formatting
- Applies rate limiting and throttling per channel
- Injects correlation IDs and trace context (OpenTelemetry) into every request

**Process APIs**
- Orchestration layer: composes multiple System API calls to build business operations
- Implements business logic: quote orchestration, policy issuance, claims intake
- Applies resilience patterns: retry with exponential backoff + jitter, circuit breaker, idempotency key validation
- Routes long-running operations to the async messaging layer (per ADR-002)
- Manages error handling and fallback responses

**System APIs**
- Thin connectors to backend systems — no business logic
- Salesforce System API: exposes Salesforce objects (Account, Opportunity, Policy__c, Claim__c) as REST resources
- Payment Gateway System API
- Document Generation System API
- Per-country legacy system connectors

#### Layer 3 — Core Systems

- **Salesforce:** Insurance core (policies, claims, customers, products) — system of record
- **Payment Gateway:** Premium collection and refunds
- **Document Service:** Policies, certificates, claims reports
- **Notification Service:** Email, SMS, push notifications
- **Per-Country Systems:** Local regulatory systems, local payment providers

#### Layer 4 — Async Messaging Layer

For long-running and high-volume flows (per ADR-002):

- Message broker (Anypoint MQ or cloud-native equivalent)
- Dead Letter Queue (DLQ) for events that fail processing
- Event replay capability for incident recovery
- Per-country consumer isolation to contain processing failures

#### Layer 5 — Observability Platform

- **Distributed Traces:** OpenTelemetry SDK on MuleSoft runtimes; trace context propagated via W3C Trace Context headers across synchronous and asynchronous boundaries
- **Centralized Logging:** Structured JSON logs aggregated in a log management platform (Splunk, ELK, or Anypoint Monitoring)
- **Metrics:** Error rate, average latency and 95th percentile (time taken by 95% of requests), circuit breaker state, retry rate, queue depth, message consumer lag — per API and per country
- **Alerts:** Alerts based on the allowed error margin of the service level agreement (SLO) that trigger on-call response

---

### Integration Patterns Applied

#### Retry with Exponential Backoff and Jitter
Applied at the Process API layer for transient failures (network timeouts, 5xx responses from System APIs). Avoids simultaneous retry avalanches during backend recovery. Formula: `wait = min(cap, base * 2^attempt) + random_jitter`.

#### Circuit Breaker
Each System API connector is wrapped by a circuit breaker. After a configurable failure threshold, the circuit opens and returns a fast-fail response — protecting Salesforce and other backends from cascading overload. States: Closed → Open → Half-Open → Closed.

#### Idempotency
Every mutating operation (policy issuance, payment processing) requires an idempotency key in the request header. The Process API layer deduplicates requests within a configurable time window, preventing duplicate records in Salesforce from retried requests.

#### Bulkhead
The bulkhead pattern applies resource isolation at two levels:

1. **Experience API layer:** Each channel has its own thread pool and rate limit. If the web portal receives a traffic spike or an attack, only its Experience API is affected — the mobile app and broker portal keep operating normally. No channel can exhaust another's resources.

2. **Process API / System layer:** MuleSoft thread pools are partitioned by country and by flow criticality. A flood of requests for Colombia does not exhaust the threads serving Venezuela. Critical flows (quotes, payments) are isolated from batch and reporting flows.

The result is that a failure or spike in any channel or country is contained — it does not propagate to the rest of the system.

#### Cache
- **Quote cache:** Product catalog and pricing cached at the Experience API layer (TTL: 5 minutes) — reduces Salesforce API calls for high-frequency quote requests
- **Token cache:** OAuth access tokens cached until expiry — eliminates per-request authentication overhead
- **Reference data cache:** Per-country configuration, product catalog, coverage rules — refreshed on schedule, not per request

#### Async Messaging
Policy issuance, claims notification, and cross-country data synchronization are routed to the async messaging layer (per ADR-002). Producers (Process APIs) publish events and return an acknowledgment immediately. Consumers process events independently with retry and DLQ support.

---

### High Availability and Scalability

- MuleSoft CloudHub 2.0 (or Runtime Fabric on-premises): active-active deployment across multiple availability zones
- Auto-scaling rules based on CPU utilization and message queue depth
- Salesforce API call volume managed through bulkhead isolation — each country has a dedicated Salesforce Connected App with its own API limit allocation
- Load balancer with health checks at the Experience API layer — unhealthy workers removed from rotation without downtime
- HA at the database layer for the idempotency key store (distributed cache, e.g. Redis) with replication

---

## 2. Architecture Diagram

![Architecture Diagram](../diagrams/architecture.png)


---

## 3. Technical Roadmap — 12 Weeks

Three parallel workstreams executed over 12 weeks:

#### Track 1 — Reliability

| Week | Activity |
|:---:|---|
| 1 | Audit existing circuit breaker and retry configurations across all MuleSoft APIs |
| 2 | Define SLOs per API (availability, p95 latency, error rate) |
| 3 | Implement bulkhead thread pool isolation per country in MuleSoft |
| 4 | Deploy idempotency key validation for all mutating Process APIs |
| 5 | Implement retry with exponential backoff + jitter for all System API calls |
| 6 | Reliability review: controlled failure tests (chaos engineering) on circuit breaker scenarios |
| 7 | Implement cache layer: product catalog, OAuth tokens |
| 8 | Load testing: validate auto-scaling and bulkhead isolation under peak traffic |
| 9 | Implement Dead Letter Queue processing and alerting |
| 10 | Controlled failure tests: simulate Salesforce degradation; validate circuit breaker and fallback |
| 11 | Multi-country HA validation: simulate availability zone failure |
| 12 | Closure: all SLOs met, all critical paths tested |

#### Track 2 — Integration Modernization

| Week | Activity |
|:---:|---|
| 1 | Inventory all existing System APIs and identify duplication across countries |
| 2 | Establish API design standards and semantic versioning policy (e.g. v1.2.3) |
| 3 | Refactor per-country duplicate connectors into shared System APIs |
| 4 | Migrate the 3 highest-traffic integrations to the new System API pattern |
| 5 | Implement async messaging for the policy issuance flow |
| 6 | Implement async messaging for the claims notification flow |
| 7 | Publish reusable Experience API templates to Anypoint Exchange |
| 8 | Migrate second batch of per-country integrations to the shared System API pattern |
| 9 | Implement idempotency on the message consumer (async deduplication) |
| 10 | API governance review: deprecate legacy direct integrations |
| 11 | Complete Anypoint Exchange asset library: all reusable APIs documented |
| 12 | Closure: no direct Salesforce integrations bypassing MuleSoft |

#### Track 3 — Observability and Operations

| Week | Activity |
|:---:|---|
| 1 | Deploy OpenTelemetry collector; instrument the top 5 critical Process APIs |
| 2 | Implement structured JSON logging with correlation ID propagation |
| 3 | Build first metrics dashboard: error rate, latency, circuit breaker state |
| 4 | Define alert rules based on the allowed error margin of the service level agreement (SLO) |
| 5 | Instrument async flows: queue depth, message consumer lag, DLQ rate |
| 6 | First on-call runbook: open circuit breaker, DLQ spike, latency degradation |
| 7 | End-to-end distributed traces: trace propagation across sync and async boundaries |
| 8 | SLO review: adjust thresholds based on 4 weeks of real operational data |
| 9 | Capacity planning report: Salesforce API limits per country, MuleSoft worker utilization |
| 10 | Finalize on-call runbook for all P1/P2 scenarios |
| 11 | Executive observability report: SLO compliance, incident trends |
| 12 | Closure: traces, logs, metrics, alerts, and runbooks fully operational |

### Workstream Summary

**Reliability** — Weeks 1–12: Establish and enforce resilience patterns (circuit breaker, retry, bulkhead, idempotency, cache) across all integration flows. Validate under real failure conditions.

**Integration Modernization** — Weeks 1–12: Consolidate fragmented per-country integrations into a shared System API layer. Eliminate direct Salesforce access. Publish reusable assets to Anypoint Exchange. Adopt async messaging for long-running flows.

**Observability and Operations** — Weeks 1–12: Instrument all critical flows with OpenTelemetry. Build dashboards, define SLOs, configure alerts, and produce operational runbooks. Transition from reactive to proactive operations.
