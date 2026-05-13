---
id: typer-default-option
slug: typer-default-option
title: Typer Default Option Signatures
status: open
created: 2026-05-13
drain_slice: async-pipeline
parent_slice: '#1175'
rule: B008
rules:
  - B008
sites: see artifacts/quality-debt-report.json
fix_class: small
---

# Typer Default Option Signatures

## Pattern

Typer CLI signatures use `param: T = typer.Option(...)` which triggers B008
(mutable default in function signature) because Ruff cannot distinguish the
Typer-prescribed pattern from a genuine mutable default anti-pattern. The right
fix is upstream — Typer 0.13+ uses the `Annotated` form which eliminates the
ambiguity. Track for a sweep when the project's minimum Typer version bumps;
suppress until then.

## Sites

See `artifacts/quality-debt-report.json` for the live site list (~10 markers).

## Drain plan

Future async debt-drain pipeline (separate epic) will schedule refactors per slug.
Once all callsites are gone, flip `status: drained` in this file.

## Notes

Originally tracked as POLICY:typer-default in #1162; reclassified as honest DEBT in #1175 per the project-wide dismantling of the POLICY/ratchet layer.
