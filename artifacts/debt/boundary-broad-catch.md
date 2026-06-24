---
id: boundary-broad-catch
slug: boundary-broad-catch
title: Boundary Broad Catch
status: drained
created: 2026-05-13
drain_slice: async-pipeline
parent_slice: '#1175'
rule: BLE001
rules:
  - BLE001
sites: 30 acknowledged boundaries in src/factory/ (ADR-073 appendix)
fix_class: medium
closed_by: '#1832'
closed: 2026-06-24
---

# Boundary Broad Catch

## Pattern

Broad `except Exception` blocks at adapter and IO boundaries swallow failures
wholesale, masking the underlying exception type from callers and logging. The
suppression is tagged as DEBT because the right fix is to introduce narrow
exception classes per call surface — one per distinct failure mode — rather than
a single catch-all that conflates network timeouts, parse errors, and auth
failures into a uniform opaque error.

## Steady state (#1832)

Burn-down complete 2026-06-24. **30** acknowledged `except Exception` sites
remain in `src/factory/` — each with inline `# boundary: <reason>`. All other
sites were narrowed, re-raised, or consolidated. See ADR-073 revisit-trigger
appendix for the canonical site list and measurement commands.

| Metric | Filing (2026-06-24) | Steady state |
|--------|---------------------|--------------|
| `except Exception` in `src/factory/` | 168 | **30** |
| Untagged BLE001 (`src/factory/`) | 50 | **0** |

## Drain plan

Future async debt-drain pipeline may schedule per-slug refactors for the 30
acknowledged wire boundaries (adapters, transport, hub-loop, cli-subprocess
#1812 deferrals). No re-baseline above 30.

## Notes

Originally tracked as POLICY:boundary in #1162; reclassified as honest DEBT in #1175 per the project-wide dismantling of the POLICY/ratchet layer. Issue #1832 closed the ADR-073 revisit trigger via Shape 3 burn-down (V1–V5).