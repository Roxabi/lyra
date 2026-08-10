---
title: Scrape placement — factory
description: Current scrape path, why hub subprocess fails in prod, and the target design (HTTP service vs agent tool).
---

# Scrape placement

> Status: LIVING design note (2026-08-01). Complements `workers-tooling.md` (tool taxonomy) and `integrations/web_intel.py` (current impl).
> Not an ADR — placement decision for web scrape only. Vault product path is **deleted** (no `/vault-add`).

## Current state (what ships today)

```
User: /explain | /summarize <url>   (bare URL → /explain when patterns.bare_url)
        │
        ▼
Hub  ScrapingProcessor.pre()     HTTPS-only + SSRF guard (private IP reject)
        │
        ▼
WebIntelScraper  (in-process in hub)
        │  subprocess: uv run python …/roxabi-plugins/plugins/web-intel/scripts/scraper.py
        ▼
playwright + trafilatura  (plugin venv under ~/projects/…)
        │
        ▼
text injected into LLM prompt   — or "[scraping unavailable]" on failure
```

| Piece | Location |
|---|---|
| Protocol | `ScrapeProvider` — `factory.integrations.base` |
| Impl | `WebIntelScraper` — `factory.integrations.web_intel` |
| Consumers | `ExplainProcessor`, `SummarizeProcessor` via `_scraping.ScrapingProcessor` |
| Plugin | `~/projects/roxabi-plugins/plugins/web-intel` (override: `FACTORY_WEB_INTEL_PATH`) |

**Prod failure mode:** `factory-hub` container has no `~/projects` mount and no web-intel image layer → `ScrapeFailed("not_available")` → user sees `[scraping unavailable]`. Fix is **placement**, not a volume hack on the hub monolithe.

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
| Deploy | Quadlet unit on M₁ (or M₂ if browser-heavy); image embeds web-intel/playwright |
| Hub change | HTTP-backed `ScrapeProvider` impl; drop subprocess from hub image |
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
2. **Done (#2327)** — scrape HTTP service: `factory scrape serve`, Quadlet
   `factory-scrape.container` (`POST /scrape`, `GET /ready` / `/health`).
   Extractor: httpx + HTML text (no host plugin). Playwright-heavy image optional later.
3. **Done (#2327)** — `HttpScrapeProvider` + `build_scrape_provider()`; hub injects
   when `FACTORY_SCRAPE_URL` is set (hub unit sets `http://factory-scrape:8455`).
4. **Done (#2327)** — container deploy path does not require `FACTORY_WEB_INTEL_PATH`
   / host `~/projects` for hub scrape. Host-dev still falls back to `WebIntelScraper`.
5. Optional: MCP or skill façade for agent runtime → same HTTP API.
6. Optional later: retire hub `/explain`/`/summarize` if product prefers pure agent tools.

---

## Invariants

- Hub does **not** spawn playwright.
- One scrape engine; many clients (hub commands, agent, future jobs).
- `ScrapeProvider` stays the in-process port; transport is an impl detail.
- Cortex memory ≠ scrape; do not re-bundle capture into scrape processors.
- code-worker is **not** a prerequisite for fixing scrape.
