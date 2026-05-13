---
id: wiring-bootstrap-deps
slug: wiring-bootstrap-deps
title: Wiring Bootstrap Dependencies
status: open
created: 2026-05-13
drain_slice: async-pipeline
parent_slice: '#1175'
rule: PLR0913
rules:
  - PLR0913
  - C901
sites: see artifacts/quality-debt-report.json
fix_class: large
---

# Wiring Bootstrap Dependencies

## Pattern

Bootstrap factories accept many positional dependencies because the composition
root passes a fully wired dependency graph manually, one argument per service.
This triggers PLR0913 (too many positional arguments) and C901 (high complexity)
because all wiring logic is inline rather than delegated. The right fix is a DI
container or a grouped config/context object that collapses the argument list and
moves composition out of the factory signatures.

## Sites

See `artifacts/quality-debt-report.json` for the live site list (~70 markers).

## Drain plan

Future async debt-drain pipeline (separate epic) will schedule refactors per slug.
Once all callsites are gone, flip `status: drained` in this file.

## Notes

Originally tracked as POLICY:wiring in #1162; reclassified as honest DEBT in #1175 per the project-wide dismantling of the POLICY/ratchet layer.
