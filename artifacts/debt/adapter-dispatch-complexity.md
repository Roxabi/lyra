---
id: adapter-dispatch-complexity
slug: adapter-dispatch-complexity
title: Adapter Dispatch Complexity
status: open
created: 2026-05-12
drain_slice: P2b
parent_slice: '#1163'
rule: C901
rules:
  - C901
sites: see artifacts/quality-debt-report.json
fix_class: needs_review
---

# Adapter Dispatch Complexity

## Pattern

Discord outbound dispatcher (`discord_outbound.py`) contains a complex routing
function that exceeds C901 thresholds. Unlike `core/` dispatchers that fit
`POLICY:wiring` (constructor wiring), this is dispatch logic where the
complexity is intrinsic to the message-type routing.

## Sites

See `artifacts/quality-debt-report.json` (currently 1 site in
`src/lyra/adapters/discord/discord_outbound.py`).

## Drain plan

1. Identify the natural seams in the dispatch (per message type / per stream
   phase).
2. Extract per-type handler functions; reduce the dispatcher to a router table.
3. Re-measure complexity; remove the noqa if under threshold.

## Notes

Surfaced during #1163. Refactor candidate, not a structural POLICY.
