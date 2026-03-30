# Section D – Technical Decision Record

---

## ADR-001: Centralized Integration Platform vs. Decentralized Team-Owned Integrations

**Date:** 2026-03-28
**Status:** Accepted
**Decision makers:** Technical Lead, Architecture Team

---

### Context

Sura operates a multi-country Digital Direct Channel where every inbound and outbound request flows through a centralized middleware layer (MuleSoft Anypoint Platform) that mediates between Salesforce (central insurance system of record) and external systems, partners, and digital channels.

As the organization scales across countries and product lines, teams face increasing pressure to deliver integrations faster. Two competing models emerge:

- **Option A:** Maintain and strengthen the centralized MuleSoft integration platform, managed by a dedicated integration team.
- **Option B:** Allow product teams to own and build their own integrations directly, reducing dependency on a central team.

This decision directly impacts delivery speed, governance, operational resilience, and long-term maintainability.

---

### Options Considered

#### Option A — Centralized Integration Platform (MuleSoft API-Led)

All integrations are built, deployed, and operated through MuleSoft Anypoint Platform following a three-layer API-led architecture:
- **Experience APIs:** Channel-specific adapters (mobile, web, broker portals)
- **Process APIs:** Orchestration and business logic
- **System APIs:** Connectors to Salesforce, core insurance systems, and external providers

A dedicated integration team owns the platform, enforces standards, and publishes reusable assets to Anypoint Exchange.

**Advantages:**
- Single compliance point for security, rate limiting, and observability
- Reusable API assets reduce duplication across countries
- Consistent logging, tracing, and error handling across all integrations
- Simpler compliance and auditability (insurance sector regulatory requirements)
- Resilience patterns (retry, circuit breaker, idempotency) applied once, inherited everywhere

**Disadvantages:**
- Central team becomes a bottleneck for high-velocity product teams
- Requires strong API design governance and versioning discipline
- Higher upfront investment in platform expertise and tooling

---

#### Option B — Decentralized Team-Owned Integrations

Each product team builds and operates its own integrations using the technology of their choice. Teams are responsible for their own reliability, security, and observability.

**Advantages:**
- Teams move faster without waiting for a central team
- Technology flexibility per team context
- Lower organizational dependency

**Disadvantages:**
- Fragmented observability — no unified view of system health
- Security and compliance gaps between teams of different maturity
- Duplication of integration logic across countries and channels
- Resilience patterns implemented inconsistently or not at all
- High operational cost: N teams operating N integration stacks
- In a regulated industry (insurance), decentralized ownership significantly increases audit complexity

---

### Decision

**We recommend Option A — Centralized Integration Platform**, with specific tactical adjustments to reduce bottlenecks.

Rationale:
1. **The regulatory context demands governance.** Insurance operations across multiple countries require consistent audit trails, data sovereignty controls, and security compliance. A centralized platform provides a single compliance perimeter.
2. **Resilience at scale requires consistency.** Patterns such as circuit breakers, idempotency, and retry with exponential backoff must be applied uniformly. Decentralized ownership produces inconsistent resilience — some teams implement it, others don't.
3. **Reuse reduces total cost.** Salesforce System APIs and core insurance Process APIs, built once, can be reused across all countries and channels. Decentralization rebuilds them per team.
4. **The bottleneck is solvable without decentralizing.** The real pain is team velocity, not the centralized model itself. The solution is an **internal open contribution model**: product teams contribute to the integration platform through governed self-service patterns, published templates, and reusable connectors — while the central team focuses on platform reliability and governance, not attending tickets from each team.

---

### Consequences

**Positive:**
- Unified observability across all integration flows
- Consistent resilience and security posture
- Reduced duplication of Salesforce and core system connectors
- Simpler compliance reporting across countries

**Negative / Mitigations:**
- The central team must act as a support team, not a control team — providing templates, accelerators, and self-service patterns to product teams
- Platform governance must be lightweight — heavy review processes defeat the purpose
- Requires investment in MuleSoft expertise and Anypoint Exchange asset library

---
---

## ADR-002: Event-Driven vs. Synchronous Request-Response for Critical Flows

**Date:** 2026-03-28
**Status:** Accepted
**Decision makers:** Technical Lead, Architecture Team

---

### Context

The Digital Direct Channel handles multiple flow types with distinct characteristics:

- **Quote generation:** The user requests an insurance quote in real time — expects a response in under 3 seconds
- **Policy issuance:** Triggers downstream processes in Salesforce, payment gateways, and document generation
- **Claims notification:** Initiates a multi-step workflow across internal teams and external adjusters
- **Cross-country data synchronization:** Policy and customer data must remain consistent across per-country Salesforce instances

The question is: which flows should be **synchronous (request-response)** and which **event-driven (async messaging)**?

---

### Options Considered

#### Option A — Synchronous Request-Response for All Flows

All operations are handled through synchronous HTTP calls via MuleSoft. The caller waits for a complete response before continuing.

**Advantages:**
- Simple programming model — easier to reason about and debug
- Immediate error feedback to the caller
- No additional messaging infrastructure required

**Disadvantages:**
- Tight coupling between systems — if the downstream fails, the entire flow fails
- Cannot handle high-volume spikes gracefully — load propagates directly to backends
- Long-running operations (claims, document generation) block the caller's thread
- Cascading failures: a slow Salesforce response degrades the entire channel

---

#### Option B — Event-Driven for Long-Running and High-Volume Flows, Synchronous for Real-Time Responses

A hybrid model where the communication pattern is selected based on the flow's characteristics:

| Flow Type | Pattern | Rationale |
|---|---|---|
| Quote generation | Synchronous | User waits for the response; must be < 3s |
| Policy issuance | Event-driven (async) | Multi-step workflow; decouples channel from backend processing |
| Claims notification | Event-driven (async) | Initiates a long-running multi-team workflow |
| Cross-country data sync | Event-driven (async) | Eventual consistency is acceptable; volume can be high |
| Authentication / session | Synchronous | Security-sensitive; requires immediate validation |

**Advantages:**
- Decouples producers from consumers — the channel remains responsive even if Salesforce is slow
- Absorbs traffic spikes through message queue buffering
- Enables retry and dead letter queue patterns for failed events
- Long-running workflows do not block user-facing threads

**Disadvantages:**
- Introduces eventual consistency — the data synchronizes with a small delay, not in real time; requires idempotency and deduplication
- More complex operational model — requires queue depth and message consumer lag monitoring
- Harder to trace the full end-to-end flow without proper correlation IDs and distributed traces

---

### Decision

**We recommend Option B — Hybrid model: event-driven for long-running and high-volume flows, synchronous for real-time user-facing responses.**

Rationale:
1. **User experience dictates synchronous flows.** Quote generation and authentication cannot be asynchronous — users expect immediate feedback. Forcing them through a queue adds latency without benefit.
2. **Backend resilience requires decoupling.** Policy issuance and claims notification trigger complex downstream workflows in Salesforce and external systems. A synchronous chain means any slow or failing downstream degrades the entire channel. An event-driven approach isolates failures to the consuming service.
3. **Queues absorb traffic spikes, not backends.** In a multi-country digital channel, peak traffic (campaign launches, renewal periods) can saturate synchronous backends. Message queues act as buffers, smoothing the load without scaling backends beyond what is necessary.
4. **Idempotency makes async safe.** Each event carries an idempotency key, enabling safe replay without duplicate processing — a requirement already present in the integration framework (Section B).

---

### Consequences

**Positive:**
- The channel remains responsive under backend degradation
- Long-running workflows are reliable and retriable
- Traffic spikes do not cascade into backend failures
- Each flow type uses the communication pattern best suited to its characteristics

**Negative / Mitigations:**
- Operational complexity increases — mitigated by centralized observability (Section A) with queue depth and message consumer lag dashboards
- Eventual consistency requires idempotency discipline — applied at the integration framework level
- End-to-end tracing requires correlation IDs propagated across sync and async boundaries — applied through OpenTelemetry trace propagation (Section B)
