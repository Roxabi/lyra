---
id: re-export-init
slug: re-export-init
title: Re-Export Init Surface
status: open
created: 2026-05-13
drain_slice: async-pipeline
parent_slice: '#1175'
rule: F401
rules:
  - F401
sites: see artifacts/quality-debt-report.json
fix_class: small
---

# Re-Export Init Surface

## Pattern

`__init__.py` files re-export symbols tagged with `# noqa: F401` because the
public API surface is curated at the package boundary. Ruff flags these as unused
imports since it cannot see the external consumer. The right fix is an explicit
`__all__` declaration so re-imports become first-class and the noqa suppression
is no longer needed.

## Sites

See `artifacts/quality-debt-report.json` for the live site list (~12 markers).

## Drain plan

Future async debt-drain pipeline (separate epic) will schedule refactors per slug.
Once all callsites are gone, flip `status: drained` in this file.

## Notes

Originally tracked as POLICY:re-export in #1162; reclassified as honest DEBT in #1175 per the project-wide dismantling of the POLICY/ratchet layer.
