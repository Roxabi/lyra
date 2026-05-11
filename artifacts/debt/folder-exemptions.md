---
slug: folder-exemptions
status: open
created: 2026-05-11
drain_slice: "#1163"
rules:
  - folder-size
---

# folder-exemptions

## Pattern

The folder-size quality gate rejects any `src/**` directory containing more than 12 Python files. Four directories currently exceed this threshold and are listed in `tools/folder_exemptions.txt`. Each entry is tagged `DEBT:folder-exemptions` with a short description referencing the issue that caused the growth.

The gate enforces domain cohesion by preventing any single directory from becoming a catch-all dumping ground. When a directory exceeds 12 files, it is a signal that the domain has outgrown its current decomposition and a subpackage extraction is warranted. The exemption file allows development to continue while the extraction is planned.

This is tagged DEBT rather than POLICY because the underlying concern (subdirectory cohesion) is structural, not contextual. Each exempt directory grew past the threshold incrementally — through ADR-driven store migrations, WorkerError additions, session management additions, and messaging pipeline extensions. The correct response in each case is either a subpackage split or a consolidation that reduces the file count. No directory is permanently exempt by design; the exemption is a scheduled drain.

## Sites

See `artifacts/quality-debt-report.json` stale_references for all 4 entries. From `tools/folder_exemptions.txt`:

- `src/lyra/core` — 15 files (#858 config dataclass extraction pending; #1020 logging_setup.py added recently)
- `src/lyra/infrastructure/stores` — 15 files (#935 ADR-048 store migration moved 4 files from core/stores into this directory)
- `src/lyra/core/cli` — 14 files (#957 cli_pool_entry.py extracted to break `_ProcessEntry` circular import)
- `src/lyra/core/messaging` — 13 files (#1016 WorkerError additions: error_extractor.py + metrics.py; #1031 tool_recap_format.py + tool_display_config.py)

## Drain plan

- `src/lyra/core` (15 files, #858): extract config dataclasses from scattered modules into `lyra.core.config` subpackage. `logging_setup.py` (#1020) is a candidate for `lyra.obs` or `lyra.monitoring` (where observability utilities live). Target: reduce to ≤12 files by moving 3 files out.
- `src/lyra/infrastructure/stores` (15 files, #935): the 4 files moved from `lyra.core.stores` under ADR-048 are the proximate cause. As ADR-059 protocol extractions land, concrete store files may consolidate (e.g., a `store_base.py` mixin reduces per-store boilerplate). Alternatively, group by domain: `lyra.infrastructure.stores.session`, `lyra.infrastructure.stores.agent`. Target: reduce to ≤12 files through consolidation or subdirectory grouping.
- `src/lyra/core/cli` (14 files, #957): `cli_pool_entry.py` was extracted to break a circular import — it is a structural necessity. The next step is to assess whether `cli_pool.py` and `cli_pool_session.py` can be merged now that `_ProcessEntry` is isolated, or whether a `cli/pool/` subpackage is the right split. Target: reduce to ≤12 files.
- `src/lyra/core/messaging` (13 files, #1016, #1031): `error_extractor.py`, `metrics.py`, `tool_recap_format.py`, and `tool_display_config.py` are recent additions. Evaluate grouping error-related files into `lyra.core.messaging.errors/` and formatting files into `lyra.core.messaging.display/`. Alternatively, move display/formatting files to a dedicated `lyra.formatting` floating module. Target: reduce to ≤12 files.
- After each directory drops to ≤12 files, remove its entry from `tools/folder_exemptions.txt`. When all 4 entries are drained, flip this registry to `status: drained`.
- Note: some entries (notably `infrastructure/stores`) may stay DEBT permanently if the ADR-059 drain pass does not reduce the store count sufficiently. In that case, document the structural reason in this `## Notes` section and reclassify to POLICY with a dedicated vocabulary entry.

## Notes

- ADR-048 (#760, #935): store migration from `lyra.core.stores` to `lyra.infrastructure.stores` is the direct cause of `infrastructure/stores` exceeding the gate.
- V4 decomposition (#760): the comment in `tools/folder_exemptions.txt` notes that `core/`, `adapters/`, and `bootstrap/` were decomposed in V4; `core/hub/` was deferred to V5 (#848). The current exemptions are the residual from those campaigns.
- V5 (#848): `core/hub/` decomposed into `middleware/`, `outbound/`, `pipeline/` — a successful prior drain that removed a folder exemption. This pattern (identify cohesive subgroup → extract to subpackage) is the playbook for the current entries.
- Issue #858: config dataclass extraction for `src/lyra/core` has been open since the V4 campaign. The config module (`lyra.config`) is a shared floating module; extracted dataclasses belong there or in `lyra.core.config`.
- Issue #1020: `logging_setup.py` was added to `lyra.core` as a convenience module; it is a candidate for `lyra.monitoring` or `lyra.obs` which already handles observability concerns.
- Lifecycle: entries graduate individually when directories drop to ≤12 files. The slug drains when `tools/folder_exemptions.txt` is empty. If any entry is reclassified as POLICY (permanent architectural exception), update `docs/quality-policy.md` and mark it drained here with a note.
