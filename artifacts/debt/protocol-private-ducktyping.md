---
id: protocol-private-ducktyping
slug: protocol-private-ducktyping
title: Protocol Private Duck-Typing
status: open
created: 2026-05-13
drain_slice: async-pipeline
parent_slice: '#1175'
rule: reportPrivateUsage
rules:
  - reportPrivateUsage
sites: see artifacts/quality-debt-report.json
fix_class: medium
---

# Protocol Private Duck-Typing

## Pattern

Protocol implementations access `_private` members of their implementor for
duck-typing checks, triggering pyright's `reportPrivateUsage`. This happens
because the Protocol interface exposes only the public contract while the
implementation detail is kept private. The right fix is to widen the Protocol
interface to include the necessary accessor, or to expose explicit public
accessors on the implementor so the private access is no longer needed.

## Sites

See `artifacts/quality-debt-report.json` for the live site list (~4 markers).

## Drain plan

Future async debt-drain pipeline (separate epic) will schedule refactors per slug.
Once all callsites are gone, flip `status: drained` in this file.

## Notes

Originally tracked as POLICY:protocol-private in #1162; reclassified as honest DEBT in #1175 per the project-wide dismantling of the POLICY/ratchet layer.
