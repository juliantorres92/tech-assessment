# Technical Lead – Practical Technical Assessment
**Candidate:** Julian Torres
**Role:** Technical Lead – Digital Direct Channel
**Company:** Sura

---

## Deliverables

This repository contains all deliverables for the practical technical assessment. Each section is documented in `docs/` and all code is in `src/`.

| Section | Deliverable | File |
|---|---|---|
| **A – Architecture & Roadmap** | End-to-end architecture + 12-week roadmap + diagram | [docs/section-a-architecture.md](docs/section-a-architecture.md) |
| **B – Integration Framework** | Reusable Python framework + design decisions | [docs/section-b-framework.md](docs/section-b-framework.md) · [src/framework.py](src/framework.py) |
| **C – Demo Service** | Reliability demo with simulated unstable upstream | [docs/section-c-demo.md](docs/section-c-demo.md) · [src/demo.py](src/demo.py) |
| **D – Technical Decision Record** | ADR: centralized vs. decentralized · event-driven vs. synchronous | [docs/section-d-adr.md](docs/section-d-adr.md) |

---

## Architecture Diagram

The diagram source is available in [diagrams/architecture.mmd](diagrams/architecture.mmd) (Mermaid format) and [diagrams/architecture.drawio](diagrams/architecture.drawio) (draw.io editable format).

![Architecture Diagram](diagrams/architecture.png)

---

## Running the Demo (Section C)

Requirements: Python 3.8+. No additional packages needed.

```bash
python src/demo.py
```

The demo starts a simulated unstable upstream (Salesforce System API) and runs a sequence of policy issuance requests, demonstrating:

- Retry with exponential backoff and jitter on transient failures (HTTP 503)
- Per-request timeout (5s) on slow upstream responses
- Circuit breaker that opens after the failure threshold
- Idempotency: duplicate requests served from cache
- Structured JSON logging with trace ID propagation

To view clean visual output without JSON logs:

```bash
python src/demo.py 2>/dev/null
```

---

## Repository Structure

```
tech-assessment/
├── docs/
│   ├── section-a-architecture.md   # Architecture and 12-week roadmap
│   ├── section-b-framework.md      # Framework design decisions
│   ├── section-c-demo.md           # Run instructions and failure scenarios
│   └── section-d-adr.md            # Technical Decision Records (2 ADRs)
├── src/
│   ├── framework.py                # Reusable integration framework
│   └── demo.py                     # Demo service with unstable upstream
├── diagrams/
│   ├── architecture.mmd            # Architecture diagram source (Mermaid)
│   ├── architecture.drawio         # Architecture diagram (draw.io editable)
│   └── architecture.png            # Architecture diagram (rendered image)
├── Technical_Lead_Assessment.pdf   # Original assessment document
├── Technical_Lead_Assessment_Submission.pdf  # Submission document
└── README.md                       # This file
```
