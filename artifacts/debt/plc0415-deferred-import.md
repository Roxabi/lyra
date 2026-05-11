---
id: plc0415-deferred-import
slug: plc0415-deferred-import
title: PLC0415 Deferred Import
status: open
created: 2026-05-11
drain_slice: P2b
parent_slice: '#1163'
rule: PLC0415
rules:
  - PLC0415
sites: see artifacts/quality-debt-report.json
fix_class: needs_review
---

# PLC0415 Deferred Import

## Pattern

Function-scope imports (`PLC0415: import-outside-toplevel`) used to break import cycles or to defer optional / heavy dependencies until first use. Each site is intentional — moving the import to module scope would either (a) reintroduce a cycle that was deliberately cut, or (b) load a heavy/optional dependency at startup that not every code path needs.

Distinction from POLICY:
- Could plausibly become `POLICY:defer-import` if the pattern stabilises across enough sites and the rationale is uniform.
- Held as DEBT for now because individual sites have different reasons (cycle-cut vs lazy-load) and merit per-site review when refactoring nearby code.

## Sites

See artifacts/quality-debt-report.json for live site list. 3 sites at creation: `core/agent/agent.py:131`, `core/hub/middleware/middleware_guards.py:112`, `core/stores/pairing_protocol.py:51`.

## Drain plan

Per-site triage when the surrounding module is next touched:

1. `core/agent/agent.py:131` — confirm cycle exists; if module structure has evolved to permit top-level import, move it up.
2. `core/hub/middleware/middleware_guards.py:112` — same check.
3. `core/stores/pairing_protocol.py:51` — same check; also evaluate whether the deferred symbol is genuinely optional or just a left-over from an earlier refactor.

If after triage all 3 sites have a shared rationale (e.g., all cycle-cuts), promote the slug to `POLICY:defer-import` and remove this DEBT file.

## Notes

Slug created in #1163 by classifier extension (commit `268f19b4` — Rule 9: `PLC0415 → DEBT:plc0415-deferred-import`). Auto-created stub; bodies enriched here as part of the /code-review fix loop on #1168.
