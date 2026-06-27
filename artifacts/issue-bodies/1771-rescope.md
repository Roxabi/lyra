**Goal:** Build the control-plane web console — FastAPI BFF + app shell + chat-per-agent UI **inside `factory-dashboard`**.

## Context (ADR-094 — 2026-06-27)

Lives in `src/factory/dashboard/` (new) + extends `src/factory/adapters/web/` (existing smoke spine from #1977). Same Quadlet unit `factory-dashboard` (was `factory-web`). Parent: #1760.

**Not** a separate adapter (#1770 superseded). Chat uses existing `WebAdapter` + inbound pipeline; BFF adds shell layout and routes for future panels.

## Scope
- App shell: agent selector + chat window, streaming via SSE (extend `web_server.py`)
- Extract/grow `src/factory/dashboard/` when SLOC gates require (BFF routes, static assets)
- Frontend stack TBD in implementation PR (HTMX/SSE vs lightweight SPA)
- Respect file-length (300 SLOC) + folder-size (15) gates

## Acceptance
- [ ] Operator opens `factory-dashboard` on Tailnet, selects agent, sends message, sees streamed reply
- [ ] Multiple agents selectable; sessions isolated per agent
- [ ] Shell ready for #1772/#1773/#1774 panel mounts
- [ ] Gates green

## Deps
- **Unblocked from #1770** (superseded — chat adapter = `WebAdapter`)
- Foundation for #1772, #1773, #1774