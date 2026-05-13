---
id: boundary-broad-catch
slug: boundary-broad-catch
title: Boundary Broad Catch
status: open
created: 2026-05-13
drain_slice: async-pipeline
parent_slice: '#1175'
rule: BLE001
rules:
  - BLE001
sites: see artifacts/quality-debt-report.json
fix_class: medium
---

# Boundary Broad Catch

## Pattern

Broad `except Exception` blocks at adapter and IO boundaries swallow failures
wholesale, masking the underlying exception type from callers and logging. The
suppression is tagged as DEBT because the right fix is to introduce narrow
exception classes per call surface — one per distinct failure mode — rather than
a single catch-all that conflates network timeouts, parse errors, and auth
failures into a uniform opaque error.

## Sites

See `artifacts/quality-debt-report.json` for the live site list (~74 markers).

## Drain plan

Future async debt-drain pipeline (separate epic) will schedule refactors per slug.
Once all callsites are gone, flip `status: drained` in this file.

## Notes

Originally tracked as POLICY:boundary in #1162; reclassified as honest DEBT in #1175 per the project-wide dismantling of the POLICY/ratchet layer.
