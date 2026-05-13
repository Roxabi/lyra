---
id: defensive-narrow-payloads
slug: defensive-narrow-payloads
title: Defensive Narrowing of Typed Payloads
status: open
created: 2026-05-13
drain_slice: async-pipeline
parent_slice: '#1175'
rule: reportGeneralTypeIssues
rules:
  - reportGeneralTypeIssues
  - reportArgumentType
sites: see artifacts/quality-debt-report.json
fix_class: medium
---

# Defensive Narrowing of Typed Payloads

## Pattern

Manual narrowing of typed payloads with `# pyright: ignore` at call sites where
the model's union shapes are wider than the call-site actually requires. Each
suppression papers over a mismatch between the declared type (a broad union) and
the concrete shape expected by the consumer. The right fix is tighter sum types
or TypedDicts at the message boundary so the narrowing becomes unnecessary.

## Sites

See `artifacts/quality-debt-report.json` for the live site list (~16 markers).

## Drain plan

Future async debt-drain pipeline (separate epic) will schedule refactors per slug.
Once all callsites are gone, flip `status: drained` in this file.

## Notes

Originally tracked as POLICY:defensive-narrow in #1162; reclassified as honest DEBT in #1175 per the project-wide dismantling of the POLICY/ratchet layer.
