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
local file cap (`# <N> files`) plus a tracking issue. The gate counts `.py` files
in the directory (non-recursive) and compares against the declared cap.

After the package rename, four legacy folder exemptions dissolved through
decomposition (#848 hub split, #1960 stores subpackages) and cap realignment
(12→15). One directory remains over the gate.

## Sites

From `tools/folder_exemptions.txt`:

- `src/factory` — 15 files (#1945 `cli_secrets.py` + `secrets_reset.py` added for
  disaster recovery; `cli_*` cluster is the proximate bloat driver)

`artifacts/quality-debt-report.json` `stale_references` is `[]`.

Top-level `src/factory/` modules (15): `cli.py`, `cli_agent.py`,
`cli_agent_create.py`, `cli_bot.py`, `cli_ops.py`, `cli_secrets.py`,
`cli_setup.py`, `cli_voice_smoke.py`, `config.py`, `errors.py`, `__init__.py`,
`__main__.py`, `ops_audit.py`, `paths.py`, `secrets_reset.py`.

## Drain plan

- **`src/factory` (15 files, at cap):** extract the `cli_*` command modules into
  `src/factory/cli/` (or `src/factory/commands/`). Keep `paths.py` at the
  top-level — it is a zero-dep data-dir resolver imported broadly across the
  package. Target: ≤14 files at `src/factory/` root so the exemption can be
  removed.
- **`src/factory/infrastructure/stores`:** #1960 landed `base/` and
  `migrations/` subpackages (22 total `.py` files, 15 at the root). No exemption
  entry — root count is exactly at the cap. Further store splits are optional
  hardening, not gate-blocking.
- After `src/factory` drops to ≤15 files without an exemption line, remove its
  entry from `tools/folder_exemptions.txt`. When the file is empty, flip this
  registry to `status: drained`.

## Notes

- Cap history: gate was 12 files pre-`stack.yml` alignment; `max_files: 15` is
  the live SSoT. `CONTRIBUTING.md` still says 12 — update when that doc is next
  touched (#1958 scoped to debt registry only).
- V4 (#760/#773): `core/`, `adapters/`, `bootstrap/` decomposed into cohesive
  subdirs.
- V5 (#848): `core/hub/` → `middleware/`, `outbound/`, `pipeline/`.
- Rename (#1956): `paths.py` added at `src/factory/` top-level;
  `factory_data_dir` → `$ROXABI_FACTORY_DIR`.
- Parent epic #1956 tracks post-rename debt-registry hygiene.