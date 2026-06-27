**Goal:** Phase-2 wire deltas for operator dashboard chat — **not** a greenfield `platform=dashboard` adapter (superseded by ADR-094).

## Context (rescoped 2026-06-27)

ADR-094: `factory-web` → `factory-dashboard` evolves the existing smoke spine (#1977). Chat already flows on `factory.inbound/outbound.web.smoke` with `WebMeta` in contracts.

This issue now tracks **only** what phase 2 needs when operator auth (#1992) lands:

## Scope (phase 2 — deferred)
- Optional `Platform.DASHBOARD` alias or rename from `web`
- Subjects `factory.inbound.dashboard.<bot_id>` / `factory.outbound.dashboard.<bot_id>` if/when `bot_id=smoke` retires
- `roster.dashboard` KV key (or migrate `roster.web`)
- ACL merge: `web-adapter` + `dashboard-reader` → single container identity

## Out of scope (ADR-094)
- New adapter package `src/factory/adapters/dashboard/` parallel to web
- Second Quadlet container
- Observability BFF routes (#1774) — not messaging-plane subjects

## Acceptance (phase 2 only)
- [ ] Contracts + fixtures for `dashboard` platform **if** phase-2 cutover executes
- [ ] Hub + ACL regen green; no duplicate adapter glue

## Deps
- Blocked by #1992 (operator auth) for production cutover
- Parent: #1760

**Supersedes original greenfield scope.** Phase 1 uses existing web platform unchanged.