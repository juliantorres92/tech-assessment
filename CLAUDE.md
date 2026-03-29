# Project Context

This repository contains the technical assessment for a **Technical Lead** vacancy at **Sura**.

## Assessment Overview

The full assessment is at [Technical_Lead_Assessment.pdf](Technical_Lead_Assessment.pdf). All written outputs must be in **English**.

### Sections

| Section | Description | Deliverable |
|---|---|---|
| A – Architecture & Roadmap | End-to-end architecture for a multi-country Digital Direct Channel (HA, scalability, resilience, observability) + integration patterns + 12-week roadmap | Written explanation + architecture diagram |
| B – Reusable Integration Framework | Code/pseudocode: retries with exponential backoff & jitter, circuit breaker, timeouts, centralized config, unified logging, OpenTelemetry trace propagation, idempotency key support | Code + design decisions explanation |
| C – Demo Service | Small service using the integration framework calling a simulated flaky upstream; demonstrates resilience under failure | Run instructions + behavior description |
| D – Technical Decision Record | ADR comparing: (1) centralized vs. decentralized integrations, (2) event-driven vs. synchronous request-response | One-page decision record (context, options, decision, consequences) |

### Final Submission
- A document with all written answers
- Code/pseudocode files for the integration framework
- One architecture diagram
