---
id: migration-sequence-bootstrap
slug: migration-sequence-bootstrap
title: Migration Sequence Bootstrap
status: open
created: 2026-05-13
drain_slice: async-pipeline
parent_slice: '#1175'
rule: C901
rules:
  - C901
  - PLR0915
sites: see artifacts/quality-debt-report.json
fix_class: large
---

# Migration Sequence Bootstrap

## Pattern

Migration sequencers perform many ordered steps inline within a single function,
triggering C901 (high cyclomatic complexity) and PLR0915 (too many statements).
The inline style exists because each step must execute in strict order with no
branching, which makes extraction feel risky. The right fix is per-step methods
with a sequential runner that calls them in order, preserving the ordering
guarantee while reducing per-function complexity.

## Sites

See `artifacts/quality-debt-report.json` for the live site list (~7 markers).

## Drain plan

Future async debt-drain pipeline (separate epic) will schedule refactors per slug.
Once all callsites are gone, flip `status: drained` in this file.

## Notes

Originally tracked as POLICY:migration-sequence in #1162; reclassified as honest DEBT in #1175 per the project-wide dismantling of the POLICY/ratchet layer.
