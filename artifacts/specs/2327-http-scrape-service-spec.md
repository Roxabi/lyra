---
title: "HTTP scrape service + ScrapeProvider (leave hub subprocess)"
description: "Dedicated scrape HTTP service + hub client; /explain and /summarize work in prod."
type: spec
status: approved
issue: 2327
tier: F-lite
date: 2026-08-10
---

## Context

- **Source:** `artifacts/frames/2327-http-scrape-service-frame.md` (approved)
- **Issue:** [#2327](https://github.com/Roxabi/roxabi-factory/issues/2327)
- **Design SSoT:** `docs/architecture/scrape-placement.md`, admission `docs/architecture/workers-tooling.md`
- **Mitigation already shipped:** #2325 (user-facing copy only)

## Intent

**Pain:** Hub `WebIntelScraper` shells out to host web-intel; prod hub container has no plugin → `ScrapeFailed("not_available")`. Discord bare URLs / `/explain` `/summarize` cannot return page content.

**Why now:** Confirmed live on M1 (2026-08-10): `#links` paste → thread OK, reply is “scraping unavailable” paraphrase; zero tool recap (scrape is hub pre, not agent tools).

## Goal

From a **container-deployed** hub, `/explain <https-url>` and bare-URL→explain succeed with **real scraped text** via an HTTP scrape service with `/ready` green — no hub `uv run` / host `~/projects` dependency.

## Users

- **Primary:** Operators pasting HTTPS links in Discord watch channels; slash `/explain` `/summarize`
- **Secondary:** Future agent/MCP callers of the same HTTP API (out of this issue’s MVP UI, same service)

## Expected Behavior

1. Operator (or converge) deploys `factory-scrape` (name TBD) on M₁ network `roxabi`.
2. `GET /ready` → 200 when process up **and** scraper importable.
3. Hub bootstrap injects HTTP-backed `ScrapeProvider` when `FACTORY_SCRAPE_URL` (or equivalent) is set.
4. User pastes `https://…` (bare_url) or `/explain https://…` → hub validates URL (HTTPS + SSRF pre-check) → `POST /scrape` → service re-validates SSRF + fetches → text injected into LLM prompt → reply with substance about the page.
5. If service down/circuit open: existing `ScrapeFailed` paths + #2325-style copy (no hang, no silent empty success).

## Data Model & Consumers

### Request / response (service)

```
POST /scrape
  body: { "url": "https://…", "timeout_s"?: number }
  200: { "success": true, "text": "…", "url": "https://…" }
  4xx/5xx: { "success": false, "error": "…", "reason": "ssrf"|"timeout"|"fetch"|"unavailable" }

GET /ready  → 200 { "ready": true } | 503
GET /health → 200 process up (optional, liveness only)
```

Text encoding: UTF-8 plain text (HTML stripped by existing web-intel/trafilatura path). Max body size: align with hub `_SAFE_SCRAPE_MAX_CHARS` (32_000) — service may truncate earlier and flag.

### Consumers

| Consumer | Fields | When |
|----------|--------|------|
| `ScrapingProcessor` (`/explain`, `/summarize`) | `text` | `pre()` inject into prompt |
| Future agent tool / MCP | same API | optional follow-up |
| Deploy health / converge | `/ready` | unit start + fleet checks |

### Hub client config

| Env / config | Meaning |
|--------------|---------|
| `FACTORY_SCRAPE_URL` | Base URL e.g. `http://factory-scrape:8080` |
| timeout defaults | match processor 30s unless overridden |

When unset: keep `WebIntelScraper` subprocess for **local dev only** (or fail closed in container — prefer fail-closed if `CONTAINER_NAME` set). Spec: **container deploy requires `FACTORY_SCRAPE_URL`**.

## Breadboard

### API affordances (service)

| ID | Affordance | Handler | Data |
|----|------------|---------|------|
| S1 | `GET /ready` | readiness: import scraper deps | process state |
| S2 | `GET /health` | liveness | process up |
| S3 | `POST /scrape` | validate URL → fetch → extract text | url → text |

### Hub affordances

| ID | Affordance | Handler | Data |
|----|------------|---------|------|
| H1 | SSRF pre-check | existing `_is_private_ip` + HTTPS in `_scraping` | url |
| H2 | `HttpScrapeProvider.scrape` | HTTP client → S3 | url → text / ScrapeFailed |
| H3 | Bootstrap inject | `agent_factory` / SessionTools | ScrapeProvider impl |
| H4 | `/explain` `/summarize` | unchanged processors | enriched msg |

### Deploy

| ID | Affordance | Handler | Data |
|----|------------|---------|------|
| D1 | Quadlet unit | `factory-scrape.container` (+ image) | ports internal only |
| D2 | Network | `roxabi` / systemd-roxabi | hub → scrape |
| D3 | Hub env | `FACTORY_SCRAPE_URL` in hub.env / unit | config |

### Wiring

```
User URL → Discord/TG adapter → Hub CommandMiddleware
  → ScrapingProcessor.pre (H1)
  → HttpScrapeProvider (H2) ──POST──▶ factory-scrape (S3)
  → inject <webpage> → agent LLM → outbound reply
```

## Slices

| # | Slice | Demo | Affordance IDs |
|---|-------|------|----------------|
| V1 | HTTP scrape service image + `/scrape` `/ready` + SSRF in service | `curl /ready` 200; `curl /scrape` example.com returns text | S1–S3 |
| V2 | `HttpScrapeProvider` + unit tests (SSRF dual, timeouts, mapping to ScrapeFailed) | pytest green offline | H2 |
| V3 | Bootstrap wire + Quadlet + hub env; docs slices 2–4 marked done | M1: `/explain https://example.com` returns content | H3–H4, D1–D3 |

V1 can land as image+unit before hub switch if env still points old path; V3 is the cutover.

## Edge Cases

| Case | Handling |
|------|----------|
| http:// URL | Reject at hub pre-check (existing) + service |
| Private / Tailscale / link-local IP | Reject both layers; test 100.64/10 |
| Redirect to private IP | Service revalidate final hop |
| Service down | ScrapeFailed unavailable + user copy |
| Timeout | ScrapeFailed timeout |
| Empty extract | ScrapeFailed subprocess_error / fetch |
| Oversized text | Truncate to 32k (+ marker) at hub and/or service |
| Local dev without service | Document: set FACTORY_SCRAPE_URL to local unit, or explicit dev-only subprocess flag |

## Success Criteria

- [ ] Scrape service container builds and runs with `GET /ready` → 200 when deps load
- [ ] `POST /scrape` returns extracted text for a public HTTPS page (e.g. example.com)
- [ ] Service rejects non-HTTPS and private/reserved IPs (incl. 100.64/10) with explicit reason
- [ ] Redirect to non-global IP is rejected
- [ ] `HttpScrapeProvider` maps HTTP errors to `ScrapeFailed` reasons used by processors
- [ ] Hub bootstrap uses HTTP provider when `FACTORY_SCRAPE_URL` set; container deploy requires it
- [ ] Hub image does not require `FACTORY_WEB_INTEL_PATH` / host `~/projects` for scrape
- [ ] Unit tests cover client + SSRF dual-check paths
- [ ] Quadlet unit + network wiring checked into `deploy/`
- [ ] On M1 after deploy: `/explain https://example.com` (or bare URL with bare_url) yields non-unavailable content
- [ ] `docs/architecture/scrape-placement.md` migration slices 2–4 marked done

## χ

none (design fixed in scrape-placement + issue body)

## Refs

- #2325 mitigation copy · #2322 vault-add kill · #2326 admission
- Supersede scrape-via-NATS intent on #1050 for placement (comment at ship time)
- Out: #2192 intel epic (product pipeline beyond this provider)
