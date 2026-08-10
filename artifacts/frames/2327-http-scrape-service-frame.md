---
title: "HTTP scrape service + ScrapeProvider (leave hub subprocess)"
issue: 2327
status: approved
tier: F-lite
date: 2026-08-10
---

## Problem

Bare HTTPS URLs in Discord `#links` (and `/explain` / `/summarize`) rewrite to hub
`ScrapingProcessor`, which shells out to web-intel via `WebIntelScraper`. In
production the hub image has **no** playwright/web-intel path → `ScrapeFailed("not_available")`
→ users get “page didn’t load / scraping unavailable” and **no** page content.

Mitigation #2325 only improved the error copy. The stage-axis problem remains: hub
must not host browser tooling. Observed 2026-08-10 on M1 after #2335: thread + reply
OK, zero tool recap, no link detail — scrape never ran.

## Who

- **Primary:** Operators / Mickael pasting URLs in Discord watch channels and using `/explain` `/summarize`
- **Secondary:** Any future agent/MCP caller that needs the same scrape engine without a second impl

## Constraints

- Stage axis: hub = thin orchestration; scrape = isolated capability (HTTP provider + `/ready`)
- Admission ontology (#2326): self-hosted HTTP + health ≠ NATS satellite by default
- SSRF dual-check: hub pre-check **and** service (HTTPS-only, non-global IPs incl. Tailscale `100.64/10`, redirect revalidation)
- Deploy on M₁ (or M₂ if browser-heavy); image embeds web-intel/playwright
- Wire existing `ScrapeProvider` protocol — do not invent a parallel API surface for hub commands

## Out of Scope

- NATS satellite / `factory.jobs.web-intel` consumer (#1050 — supersede intent for scrape placement)
- code-worker as scrape host (#1044 / #1046)
- Capture / vault write paths (#2322 removed)
- MCP façade (optional follow-up slice 5)
- Retiring hub `/explain`/`/summarize` in favour of pure agent tools (optional later)

## Premise Validity

**Success in 6 months:** From a prod Discord `#links` paste or `/explain <url>`, users get a
real page summary/explanation; hub logs show HTTP scrape to a dedicated service with
`/ready` green; no `uv run` / host `~/projects` dependency in hub image.

**Failure in 6 months:** Still `ScrapeFailed("not_available")` in hub after “deploy,” or
scrape only works on a developer machine with host mounts; hub still spawns playwright.

**Simplest alternative:** Mount `~/projects` + web-intel into hub (or run scraper on host).
**Why not simplest:** Fixes symptom, keeps hub as scrapers runtime, violates stage-axis and
admission design; breaks on every image-only deploy.

## Complexity

**Tier: F-lite** — size:F-lite label; single domain (scrape HTTP service + thin hub client +
Quadlet); design already written in `scrape-placement.md`.

Signals:
- Issue labels: feature, infra, size:F-lite, architecture, P1-high
- Clear acceptance checklist on issue body
- Related older NATS path (#1050, #2192) explicitly out of primary scope
