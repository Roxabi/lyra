---
id: folder-exemptions
slug: folder-exemptions
title: Folders exceeding the 15-file gate
status: drained
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
subpackage, #1960 stores subpackages), no directory currently exceeds the gate.
`tools/folder_exemptions.txt` is empty.

## Sites

`tools/folder_exemptions.txt` is currently empty — no active folder-size exemptions.

Live counts (post-#1959):

- `src/factory/` — 5 files (`config`, `errors`, `paths`, `__init__`, `__main__`)
- `src/factory/cli/` — 12 files (command modules moved from package root in #1959)
- `src/factory/infrastructure/stores/` — 15 files at root plus `base/`, `identity/`,
  `jobs/`, `kv/`, `migrations/`, `registry/`, `session/` subpackages (#1960)

`artifacts/quality-debt-report.json` `stale_references` is `[]` for this slug.

## Drain plan

No active exemptions. When a folder exceeds 15 files during development:

1. Add `<path>  # <N> files — DEBT:folder-exemptions — #<issue> <rationale>` to
   `tools/folder_exemptions.txt`.
2. Flip this registry to `status: open` and document the site + drain steps here.
3. Schedule a subpackage split in the cited issue; remove the line when the folder
   drops to ≤15 files.
4. When `tools/folder_exemptions.txt` is empty again, flip back to `status: drained`.

Recent drains (no longer listed in the exemption file):

- `src/factory` root bloat — resolved by #1959 (`factory/cli/` subpackage).
- Legacy four-folder set from pre-rename layout — resolved by #848 + cap realignment.

## Notes

- Cap history: gate was 12 files pre-`stack.yml` alignment; `max_files: 15` is
  the live SSoT.
- V4 (#760/#773): `core/`, `adapters/`, `bootstrap/` decomposed.
- V5 (#848): `core/hub/` → `middleware/`, `outbound/`, `pipeline/`.
- Parent epic #1956 tracks post-rename debt-registry hygiene.