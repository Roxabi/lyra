---
title: Scrape placement — factory
description: Current scrape path, why hub subprocess fails in prod, and the target design (HTTP service vs agent tool).
---

# Scrape placement

> Status: LIVING design note (2026-08-10). Complements `workers-tooling.md` (tool taxonomy) and `integrations/web_intel.py` / `http_scrape.py`.
> Not an ADR — placement decision for web scrape only. Vault product path is **deleted** (no `/vault-add`).

## Ownership (locked — option 2)

| Piece | Owner |
|-------|--------|
| Scrape **service + image + web-intel engine** | **roxabi-intel** (`services/scrape`, `plugins/web-intel`) → `ghcr.io/roxabi/intel-scrape` |
| `HttpScrapeProvider` + `FACTORY_SCRAPE_URL` + Quadlet **pin** | **roxabi-factory** |
| Hub / Discord / agents | consumers only |

Factory in-tree `scrape_service/` (httpx extract) is **transitional** for local/dev; prod Quadlet runs the **intel** image.

## Current state (what ships today)

```
User: /explain | /summarize <url>   (bare URL → /explain when patterns.bare_url)
        │
        ▼
Hub  ScrapingProcessor.pre()     HTTPS-only + SSRF guard (private IP reject)
        │
        ▼
HttpScrapeProvider  (FACTORY_SCRAPE_URL=http://factory-scrape:8455)
        │  POST /scrape
        ▼
factory-scrape Quadlet  →  ghcr.io/roxabi/intel-scrape  (web-intel + Playwright)
        │
        ▼
text injected into LLM prompt   — or "[scraping unavailable]" on failure
```

| Piece | Location |
|---|---|
| Protocol | `ScrapeProvider` — `factory.integrations.base` |
| Prod impl | `HttpScrapeProvider` — `factory.integrations.http_scrape` |
| Host-dev fallback | `WebIntelScraper` — `factory.integrations.web_intel` (when `FACTORY_SCRAPE_URL` unset) |
| Consumers | `ExplainProcessor`, `SummarizeProcessor` via `_scraping.ScrapingProcessor` |
| Engine | Roxabi/roxabi-intel `plugins/web-intel` (image: `ghcr.io/roxabi/intel-scrape`) |

**Prod failure mode (historical):** hub subprocess to host `~/projects` → missing in container. Fixed by HTTP service + intel image (#2327 pipe, #2338 engine).

**Out of scope / removed:** `/vault-add`, `/add-vault`, bare-URL→vault. Memory writes = cortex (ADR-087), not hub scrape side-effects.

---

## Target design

### Principle

| Layer | Owns |
|---|---|
| **Hub** | Auth, pool, command parse, thin orchestration — **not** playwright |
| **Scrape capability** | Isolated process with browser deps + `/ready` |
| **Agent runtime** (clipool/omp) | Optional agent-facing tool (CLI/MCP) with same capability |

Scrape is a **provider** (tool-nature), not a NATS “satellite GPU pattern” by default.

### Recommended path: **HTTP scrape service** (multi-caller)

```
/explain|/summarize (hub)  ──POST /scrape──▶  scrape service
agent tool / MCP           ──POST /scrape──▶  (same)
                                 │
                                 ├─ GET /ready   (liveness + playwright ok)
                                 └─ POST /scrape {url} → {text|error}
```

| Concern | Choice |
|---|---|
| Transport | HTTPS (or localhost HTTP on pod network) |
| Health | `GET /ready` — process up **and** scraper importable; hub/client circuit-breaker on call failures |
| SSRF | **Dual-check required**: hub cheap pre-check **and** service enforces HTTPS-only + non-global IP + redirect revalidation |
| Deploy | Quadlet `factory-scrape` on M₁ pins **intel** image (`ghcr.io/roxabi/intel-scrape`) |
| Hub change | HTTP-backed `ScrapeProvider` impl; drop subprocess from hub image |
| Engine home | **roxabi-intel** — factory never embeds Chromium |
| Agent | Skill/CLI optional wrapper calling same HTTP API — **one** scrape engine |

**Why not NATS satellite first?** No GPU multi-worker routing need; request/response + health is enough. Revisit NATS only if multi-instance queue + fleet registry become real requirements.

### Alternative: **agent tool only** (if hub commands die)

- Drop `/explain` `/summarize` processors from hub.
- web-intel on PATH (or MCP) **inside clipool/omp** mounts.
- User asks the agent to explain a URL; agent calls tool.
- Simplest ops if slash-commands are not product-critical.

### Rejected

| Option | Why not |
|---|---|
| Mount `~/projects` on hub | Fixes symptom; hub stays a scrapers runtime |
| `factory-code-worker` as scrape host | Code-worker **does not exist** in deploy; wrong abstraction for one HTTP capa |
| New NATS satellite “scrape” by default | Overkill vs HTTP + `/ready` |
| Revive vault-add as scrape+write | Vault product removed; capture = cortex |

---

## Health without NATS heartbeat

Same problem as other HTTP providers (e.g. Postiz): **liveness ≠ “last request succeeded”.**

| Signal | Mechanism |
|---|---|
| Process up | `GET /health` → 200 |
| Can scrape | `GET /ready` (deps loaded; optional cheap self-check) |
| Call quality | Client circuit-breaker on timeouts/5xx (mirror `WorkerPoolClient` CB) |

Heartbeat push is optional candy; pull `/ready` + CB is enough.

---

## Migration slices (suggested)

1. **Done** — remove vault-add product path; bare URL → `/explain`.
2. **Done (#2327)** — scrape HTTP placement: contract + `HttpScrapeProvider` + Quadlet unit
   + transitional in-tree extract (`factory scrape serve` / staging-svc).
3. **Done (#2327)** — hub injects HTTP client when `FACTORY_SCRAPE_URL` is set
   (`http://factory-scrape:8455`); fail-closed in container without it.
4. **Done (#2338 / intel#27)** — engine ownership: **roxabi-intel** publishes
   `ghcr.io/roxabi/intel-scrape` (web-intel + Playwright); factory Quadlet **pins**
   that image (not factory Chromium layer).
5. Optional: drop in-tree `scrape_service` extract once intel image is sole backend.
6. Optional: MCP or skill façade for agent runtime → same HTTP API.
7. Optional later: retire hub `/explain`/`/summarize` if product prefers pure agent tools.

---

## Invariants

- Hub does **not** spawn playwright.
- One scrape engine; many clients (hub commands, agent, future jobs).
- `ScrapeProvider` stays the in-process port; transport is an impl detail.
- Cortex memory ≠ scrape; do not re-bundle capture into scrape processors.
- code-worker is **not** a prerequisite for fixing scrape.
