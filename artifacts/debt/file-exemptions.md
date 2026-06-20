---
id: file-exemptions
slug: file-exemptions
title: Files exceeding the 300-SLOC gate
status: drained
created: 2026-05-11
drain_slice: "#1958"
parent_slice: '#1162'
rule: file-length
rules:
  - file-length
sites: see tools/file_exemptions.txt
fix_class: hard
---

# file-exemptions

## Pattern

The file-length quality gate rejects any `src/**/*.py` file exceeding 300 source lines
of code (SLOC). The cap and metric are configured in `.claude/stack.yml`
(`quality_gates.file_length.max_lines: 300`, `metric: sloc`) and enforced by
`tools/check_file_length.sh` via `radon`.

Exemptions are listed in `tools/file_exemptions.txt`. Each entry declares a local
SLOC cap (`# <N> lines`) plus a tracking issue. The gate still measures the live
file — the comment count is informational, not a bypass.

Prior to the package rename and the wc→SLOC metric switch, ten files were exempt
under the old tree layout. Under SLOC@300, no `src/factory/**/*.py` file currently
qualifies (max observed ~291 SLOC). `tools/file_exemptions.txt` is empty.

## Sites

`tools/file_exemptions.txt` is currently empty — no active file-length exemptions.

`artifacts/quality-debt-report.json` `stale_references` is `[]` for this slug.

## Drain plan

No active exemptions. When a file exceeds 300 SLOC during development:

1. Add `<path>  # <N> lines — DEBT:file-exemptions — #<issue> <rationale>` to
   `tools/file_exemptions.txt`.
2. Flip this registry to `status: open` and document the site + drain steps here.
3. Schedule extraction work in the cited issue; remove the line when the file
   drops below 300 SLOC.
4. When `tools/file_exemptions.txt` is empty again, flip back to `status: drained`.

## Notes

- Metric switch: raw `wc -l` counts inflated exemptions (docstrings, blanks). SLOC
  dissolved the prior ten-entry list without refactors — re-confirmed after the
  factory rename (#1958).
- Threshold source: `stack.yml` → `qg.conf` (`QG_FILE_MAX`, `QG_FILE_METRIC`).
- Format: `<path>  # <N> lines — DEBT:file-exemptions — <issue> <desc>` per
  `tools/AGENTS.md`.
- Parent epic #1956 tracks post-rename debt-registry hygiene.