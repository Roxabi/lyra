---
id: folder-exemptions
slug: folder-exemptions
title: Folders exceeding the 15-file gate
status: drained
created: 2026-05-11
drain_slice: "#1962"
parent_slice: '#1162'
rule: folder-size
rules:
  - folder-size
sites: see tools/folder_exemptions.txt
fix_class: hard
---

# folder-exemptions

## Pattern

The folder-size quality gate rejects any `src/**` directory containing more than 15
Python files. The cap is configured in `.claude/stack.yml`
(`quality_gates.folder_size.max_files: 15`) and enforced by
`tools/check_folder_size.sh`.

Exemptions are listed in `tools/folder_exemptions.txt`. Each entry declares a
local file cap (`# <N> files`) plus a tracking issue.

## Sites

`tools/folder_exemptions.txt` is currently **empty** — no active folder-size
exemptions.

Drained entries (historical):

- `src/factory` root — #1959 moved CLI into `factory/cli/` (5 files remain at root).
- `src/factory/adapters/shared` — #1962 moved inbound glue into `shared/inbound/`
  (12 files remain at `adapters/shared` root).

## Notes

- Cap history: gate was 12 files pre-`stack.yml` alignment; `max_files: 15` is
  the live SSoT.
- Parent epic #1956 tracks post-rename debt-registry hygiene.
- Slug drained when `tools/folder_exemptions.txt` became empty (#1962).