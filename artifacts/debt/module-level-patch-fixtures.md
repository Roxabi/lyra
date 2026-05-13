---
id: module-level-patch-fixtures
slug: module-level-patch-fixtures
title: Module-Level Patch Fixtures
status: open
created: 2026-05-13
drain_slice: async-pipeline
parent_slice: '#1175'
rule: F401
rules:
  - F401
  - E402
sites: see artifacts/quality-debt-report.json
fix_class: small
---

# Module-Level Patch Fixtures

## Pattern

Module-level `monkeypatch` calls and `sys.path` edits appear before imports in
test bootstrap files, triggering E402 (module-level import not at top of file)
and F401 (imported but unused) because the patch must happen before the target
module is loaded. The right fix is to move these patches into `conftest.py`
fixtures with `autouse=True`, which ensures correct load ordering without
requiring top-of-file manipulation.

## Sites

See `artifacts/quality-debt-report.json` for the live site list (~3 markers).

## Drain plan

Future async debt-drain pipeline (separate epic) will schedule refactors per slug.
Once all callsites are gone, flip `status: drained` in this file.

## Notes

Originally tracked as POLICY:module-level-patch in #1162; reclassified as honest DEBT in #1175 per the project-wide dismantling of the POLICY/ratchet layer.
