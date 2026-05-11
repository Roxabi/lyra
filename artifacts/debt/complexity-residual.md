---
id: complexity-residual
slug: complexity-residual
title: Complexity Residual
status: open
created: 2026-05-12
drain_slice: P2b
parent_slice: '#1163'
rule: C901
rules:
  - C901
  - PLR0912
  - PLR0915
  - PLR0913
sites: see artifacts/quality-debt-report.json
fix_class: needs_review
---

# Complexity Residual

## Pattern

Catch-all for cyclomatic / branch / statement / argument-count violations (C901, PLR0912, PLR0915, PLR0913) on functions that did not fit an existing POLICY tag (`wiring`, `migration-sequence`, `boundary`) yet are not obvious refactor targets either. Each site is a function that is "too big" but whose complexity is load-bearing — splitting it would just shuffle branches across helpers without reducing total cognitive load.

Distinction from POLICY:
- `POLICY:wiring` covers factory/builder/dispatcher arg-count complexity (1:1 mapping to construction inputs).
- `POLICY:migration-sequence` covers bootstrap entry-point step complexity (linear init sequence).
- This slug covers the residual: business-logic complexity that hasn't earned a vocab tag.

## Sites

See artifacts/quality-debt-report.json for live site list. 34 sites at creation across `agents/`, `bootstrap/`, `core/agent/`, `core/cli/`, `core/hub/`, `core/messaging/`, `core/persona.py`, `core/pool/`.

## Drain plan

Per-site triage (P2b):

1. Re-check whether the function actually fits an existing POLICY vocab tag (re-classify if so).
2. If not, decide refactor vs accept-as-debt:
   - Refactor candidate: split when ≥2 cohesive sub-steps exist and helper extraction reduces aggregate complexity. Extract pure-function helpers first.
   - Accept-as-debt: keep the noqa with this slug; revisit when the function changes for an unrelated reason.
3. When a site is refactored away (no longer emits the rule), the site naturally falls off the registry on the next `make quality-debt-report` run.
4. When all 34 sites drain, flip `status: open → drained` (don't delete the file).

## Notes

Slug created in #1163 by `_ensure_registry` when the classifier's aggressive rule-only fallbacks tagged complexity-rule rows that didn't match `_is_wiring_path` or bootstrap-entry heuristics. Distinct from `adapter-dispatch-complexity` (which is specifically Discord dispatcher C901).
