---
id: lint-residual
slug: lint-residual
title: Lint Residual
status: open
created: 2026-05-12
drain_slice: P2b
parent_slice: '#1163'
rule: A002
rules:
  - A002
  - E501
  - I001
  - return-value
sites: see artifacts/quality-debt-report.json
fix_class: needs_review
---

# Lint Residual

## Pattern

Catch-all for low-severity lint violations that don't have a structural POLICY tag and don't warrant a dedicated DEBT slug. Currently four rules: `A002` (builtin shadowing), `E501` (line too long), `I001` (import sorting), and `return-value` (pyright return-type mismatch). Each site is a narrow cosmetic or near-cosmetic exception with no behavioral consequence.

Distinction from POLICY:
- These rules don't correspond to a recognisable architectural pattern (unlike `boundary`, `wiring`, `re-export`).
- Each occurrence has a one-off reason (e.g., `id` as a parameter name in a function mirroring an external API; an `E501` in a long URL string).

## Sites

See artifacts/quality-debt-report.json for live site list. 4 sites at creation: `adapters/telegram/telegram.py:22`, `core/cli/cli_pool.py:49`, `core/hub/middleware/middleware_submit.py:37`, `core/messaging/callbacks.py:31`.

## Drain plan

Per-site triage (opportunistic, no deadline):

1. `A002`: rename the offending parameter if it doesn't break an external contract; otherwise keep the suppression.
2. `E501`: split the string / line if readable; otherwise keep (long URLs / structured strings are valid).
3. `I001`: should already be auto-fixed by `ruff format` / pre-commit; if a site persists, investigate why ruff isn't normalising it.
4. `return-value`: tighten the return annotation or add an explicit cast at the boundary; this is the only rule in the set that could mask a real type bug — review each instance.

When the residual count for any single rule drops to zero, consider promoting that rule to its own POLICY tag or removing it from the `rules:` list.

## Notes

Slug created in #1163 by `_ensure_registry` (commit `b8a626f8`) as the catch-all for rule-only fallback classifications that didn't fit an existing tag. Broader than ideal — future iterations may split it (e.g., `lint-line-length` for E501) once site counts justify the per-rule registry overhead.
