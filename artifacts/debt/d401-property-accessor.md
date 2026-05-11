---
id: d401-property-accessor
slug: d401-property-accessor
title: D401 Property Accessor
status: open
created: 2026-05-12
drain_slice: P2b
parent_slice: '#1163'
rule: D401
rules:
  - D401
sites: see artifacts/quality-debt-report.json
fix_class: easy
---

# D401 Property Accessor

## Pattern

`@property` accessor docstrings flagged by D401 ("imperative mood"). For
property accessors that delegate to another attribute, the docstring naturally
describes the value (declarative) rather than an action (imperative). The
pydocstyle rule does not fit this idiom.

## Sites

See `artifacts/quality-debt-report.json` (currently in
`src/lyra/core/agent/agent.py` accessors for `_effective_plugins` and
`_plugin_mtimes`).

## Drain plan

Either:
1. Rewrite the property docstrings in imperative mood and drop the noqa, OR
2. Promote the slug to `POLICY:property-alias` after one more occurrence
   demonstrates it as a recurring structural choice rather than a one-off.

## Notes

Surfaced during #1163. Sits between POLICY (structural justification) and
true DEBT (refactor candidate). Tag this slug because we don't yet have enough
occurrences to justify a POLICY entry in the vocab.
