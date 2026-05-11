---
id: adapter-magic-constants
slug: adapter-magic-constants
title: Adapter Magic Constants
status: open
created: 2026-05-12
drain_slice: P2b
parent_slice: '#1163'
rule: PLR2004
rules:
  - PLR2004
sites: see artifacts/quality-debt-report.json
fix_class: medium
---

# Adapter Magic Constants

## Pattern

Discord adapter call-sites use raw numeric literals (Discord API caps, role IDs,
message length thresholds) rather than named module-level constants. Tagged as
DEBT because the right fix is to hoist named constants per protocol/intent, not
to suppress the warning.

## Sites

See `artifacts/quality-debt-report.json` for the live site list (typically in
`src/lyra/adapters/discord/discord_audio.py`, `discord_formatting.py`).

## Drain plan

1. Identify each magic literal's semantic role (Discord API doc reference).
2. Add a named `_DISCORD_<NAME>` constant at the module top.
3. Replace the literal with the constant; remove the noqa.

## Notes

Surfaced during #1163 drain pass. Low priority — these are isolated and
non-blocking, but each fix is a small protocol-doc audit.
