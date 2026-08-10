---
title: "Plan: HTTP scrape service + ScrapeProvider"
issue: 2327
spec: artifacts/specs/2327-http-scrape-service-spec.md
complexity: 6/10
tier: F-lite
generated: 2026-08-10
---

## Summary

Ship an isolated HTTP scrape service (playwright/web-intel) and thin hub
`HttpScrapeProvider` so `/explain` and bare-URL work in prod containers. Hub stays
orchestration-only; no NATS scrape job; SSRF dual-check.

## Architecture

**Axis:** stage stays hub command + agent run; scrape = **HTTP provider** (not
adapter duplication, not satellite).

```
adapter → hub (H1 SSRF pre, H2 HttpScrapeProvider) → factory-scrape (S3)
                → inject → omp/clipool → outbound
```

**Data flow (prose):** see spec §Breadboard. **Visuals:** deferred (F-lite; optional forge later).

**Refs (conventions):**
- `src/factory/integrations/web_intel.py` — current ScrapeProvider impl
- `src/factory/core/processors/_scraping.py` — SSRF pre + fallback copy
- `deploy/quadlet/factory-blobstore.container` — HTTP unit pattern (network, secrets)
- `docs/architecture/scrape-placement.md` — placement SSoT

## Agents

| Agent | Tasks | Files |
|-------|-------|-------|
| backend-dev-A | T1–T4 service API + SSRF | `services/scrape/` or `deploy/scrape/` + app |
| backend-dev-B | T5–T7 client + bootstrap | `integrations/http_scrape.py`, `agent_factory.py` |
| devops-A | T8–T9 Quadlet + env | `deploy/quadlet/`, hub env |
| tester-A | T10–T12 tests | `tests/integrations/`, `tests/core/` |
| doc-writer-A | T13 docs | `scrape-placement.md`, runbook line |

## Wave Structure

3 waves, max 3 parallel. Elapsed ~ sequential 1.5–2d of focused work.

| Wave | Trigger | Agents | Tasks |
|------|---------|--------|-------|
| 1 | start | backend-dev-A ∥ tester-A | T1,T2 service skeleton + RED SSRF tests |
| 2 | Wave 1 | backend-dev-A → T3,T4; backend-dev-B T5,T6; tester-A T10 | implement service + client |
| 3 | Wave 2 | devops-A T8,T9; backend-dev-B T7; tester-A T11,T12; doc T13 | deploy wire + hub inject + docs |

### Budget — per task

| Task | Class | Est. ops | Split? |
|------|-------|----------|--------|
| T1 service skeleton | bounded | 6 | — |
| T2 SSRF service | judgmental | 8 | — |
| T3 scrape handler | judgmental | 10 | — |
| T4 ready/health | bounded | 4 | — |
| T5 HttpScrapeProvider | bounded | 6 | — |
| T6 ScrapeFailed map | bounded | 4 | — |
| T7 bootstrap wire | bounded | 5 | — |
| T8 Quadlet | judgmental | 8 | — |
| T9 hub env FACTORY_SCRAPE_URL | trivial | 2 | — |
| T10 unit client tests | bounded | 6 | — |
| T11 unit SSRF dual | judgmental | 8 | — |
| T12 processor integration mock | bounded | 5 | — |
| T13 docs slices 2–4 | bounded | 4 | — |

**Total estimated ops: ~76** (ok for F-lite multi-wave)

### Budget — per agent instance

| Instance | Tasks | Σ ops | Subjects | Split? |
|----------|-------|-------|----------|--------|
| backend-dev-A | T1–T4 | 28 | service,ssrf | — |
| backend-dev-B | T5–T7 | 15 | client,bootstrap | — |
| devops-A | T8–T9 | 10 | deploy | — |
| tester-A | T10–T12 | 19 | tests | — |
| doc-writer-A | T13 | 4 | docs | — |

## Task Seeding Blueprint

### Wave 1 — no deps, 2 agents ∥

| Task | Agent instance | blockedBy | Subject |
|------|---------------|-----------|---------|
| T1 | backend-dev-A | — | service |
| T2 | tester-A | — | ssrf |

### Wave 2 — after Wave 1

| Task | Agent instance | blockedBy | Subject |
|------|---------------|-----------|---------|
| T3 | backend-dev-A | T1,T2 | service |
| T4 | backend-dev-A | T1 | service |
| T5 | backend-dev-B | T1 | client |
| T6 | backend-dev-B | T5 | client |
| T10 | tester-A | T5 | tests |

### Wave 3 — after Wave 2

| Task | Agent instance | blockedBy | Subject |
|------|---------------|-----------|---------|
| T7 | backend-dev-B | T5,T6 | bootstrap |
| T8 | devops-A | T3,T4 | deploy |
| T9 | devops-A | T8 | deploy |
| T11 | tester-A | T2,T5 | tests |
| T12 | tester-A | T7 | tests |
| T13 | doc-writer-A | T7,T8 | docs |

## Micro-Tasks

### V1 — Scrape service

**T1** — Scaffold scrape HTTP app (FastAPI or existing stack pattern: lightweight ASGI).  
Files: `services/scrape/` or `src/factory/scrape_service/` (prefer deployable path under repo; image Dockerfile).  
Verify: `python -c "import …"` / unit boot.  
Agent: backend-dev-A · Subject: service · Slice: V1 · Phase: GREEN

**T2** — RED tests: reject `http://`, private IPs, `100.64.0.0/10`, redirect-to-private (mock).  
Files: `tests/scrape/` or `tests/integrations/test_scrape_service_ssrf.py`  
Verify: pytest fails before impl, passes after T3.  
Agent: tester-A · Subject: ssrf · Slice: V1 · Phase: RED

**T3** — Implement `POST /scrape`: validate URL → call web-intel/playwright extract → JSON text; map errors to `reason`.  
Files: service handlers  
Verify: manual/curl example.com in container later; unit with mocked extractor.  
Agent: backend-dev-A · Subject: service · Slice: V1

**T4** — `GET /ready` (import scraper OK) + optional `GET /health`.  
Agent: backend-dev-A · Subject: service · Slice: V1

### V2 — Hub client

**T5** — `HttpScrapeProvider` implementing `ScrapeProvider` via httpx; env `FACTORY_SCRAPE_URL`.  
Files: `src/factory/integrations/http_scrape.py`  
Agent: backend-dev-B · Subject: client · Slice: V2

**T6** — Map HTTP status/body → `ScrapeFailed("not_available"|"timeout"|"subprocess_error")` compatible with `_scraping._scrape_with_fallback`.  
Agent: backend-dev-B · Subject: client · Slice: V2

**T10** — Unit tests for HttpScrapeProvider (respx/httpx mock).  
Agent: tester-A · Subject: tests · Slice: V2

### V3 — Wire + deploy

**T7** — Bootstrap: if `FACTORY_SCRAPE_URL` set (or always in container), inject `HttpScrapeProvider` into SessionTools; else keep WebIntelScraper for host-dev only.  
Files: `src/factory/bootstrap/factory/agent_factory.py` (+ any wiring helpers)  
Agent: backend-dev-B · Subject: bootstrap · Slice: V3

**T8** — Quadlet `factory-scrape.container` (+ Dockerfile/image tag strategy: dedicated image or extend base with playwright). Network `roxabi`. Internal port only.  
Files: `deploy/quadlet/factory-scrape.container`, Dockerfile  
Agent: devops-A · Subject: deploy · Slice: V3

**T9** — Hub unit/env: `FACTORY_SCRAPE_URL=http://factory-scrape:<port>`; install.sh / converge if needed.  
Agent: devops-A · Subject: deploy · Slice: V3

**T11** — Expand SSRF tests for dual-check (hub existing + service).  
Agent: tester-A · Subject: tests · Slice: V3

**T12** — Integration test: ScrapingProcessor with mocked HttpScrapeProvider returns enriched msg.  
Agent: tester-A · Subject: tests · Slice: V3

**T13** — Mark scrape-placement slices 2–4 done; short runbook line for scrape unit.  
Files: `docs/architecture/scrape-placement.md`, optional runbook  
Agent: doc-writer-A · Subject: docs · Slice: V3

### RED-GATE V1
After T3–T4: service `/ready` + `/scrape` mocked extractor green in CI unit tests.

### RED-GATE V3
After T7–T12: qg + pytest; M1 smoke deferred to ops after image publish.

## Consistency Report

| Spec SC | Tasks |
|---------|-------|
| service /ready | T4, T8 |
| POST scrape text | T3 |
| SSRF service | T2, T3, T11 |
| redirect private | T2, T3 |
| HttpScrapeProvider map | T5, T6, T10 |
| bootstrap FACTORY_SCRAPE_URL | T7, T9 |
| no host plugin in prod path | T7, T13 |
| unit tests | T10–T12 |
| Quadlet | T8 |
| M1 smoke | ops after merge (not code task) |
| docs slices 2–4 | T13 |

Covered: 11/11 criteria mapped. Uncovered: none (M1 smoke = post-ship ops).

## Non-goals (restate)

- NATS `factory.jobs.web-intel.scrape` as primary transport  
- Nika / generic workflow engine for this ticket  
- Hub still running playwright  

---

<!-- ## Task IDs filled on plan approve -->

## Task IDs

<!-- Generated by /plan. Used by /implement to resume tasks on session restart. -->
- T1: plan-2327-t1 — service
- T2: plan-2327-t2 — ssrf
- T3: plan-2327-t3 — service
- T4: plan-2327-t4 — service
- T5: plan-2327-t5 — client
- T6: plan-2327-t6 — client
- T7: plan-2327-t7 — bootstrap
- T8: plan-2327-t8 — deploy
- T9: plan-2327-t9 — deploy
- T10: plan-2327-t10 — tests
- T11: plan-2327-t11 — tests
- T12: plan-2327-t12 — tests
- T13: plan-2327-t13 — docs
