---
id: folder-exemptions
slug: folder-exemptions
title: Folders exceeding the 15-file gate
status: open
created: 2026-05-11
drain_slice: "#1958"
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

After the package rename and subsequent decomposition (#848 hub split, #1959 CLI
subpackage, #1960 stores subpackages), one directory still exceeds the gate.

## Sites

From `tools/folder_exemptions.txt`:

- `src/factory/adapters/shared` — 16 files (#1931 inbound context + pipeline glue
  kit extraction: `inbound_context.py`, `inbound_pipeline.py`, `platform_meta.py`,
  `typing_shim.py`, plus existing shared adapter helpers)

`artifacts/quality-debt-report.json` `stale_references` is `[]` for this slug.

Drained since last registry refresh:

- `src/factory` root — #1959 moved CLI into `factory/cli/` (5 files remain at root).

## Drain plan

- **`src/factory/adapters/shared` (16 files, #1931):** split inbound pipeline
  glue (`inbound_context.py`, `inbound_pipeline.py`, `platform_meta.py`,
  `typing_shim.py`) into `src/factory/adapters/shared/inbound/` or a dedicated
  `src/factory/adapters/_glue/` package. Target: ≤15 files so the exemption line
  can be removed.
- After the folder drops to ≤15 files, remove its entry from
  `tools/folder_exemptions.txt`. When the file is empty, flip this registry to
  `status: drained`.

## Notes

- Cap history: gate was 12 files pre-`stack.yml` alignment; `max_files: 15` is
  the live SSoT.
- V4 (#760/#773): `core/`, `adapters/`, `bootstrap/` decomposed.
- V5 (#848): `core/hub/` → `middleware/`, `outbound/`, `pipeline/`.
- #1959: `factory/cli/` subpackage — drained the former `src/factory` root exemption.
- Parent epic #1956 tracks post-rename debt-registry hygiene.