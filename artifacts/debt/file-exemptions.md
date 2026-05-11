---
id: file-exemptions
slug: file-exemptions
title: Files exceeding the 300-line gate
status: open
created: 2026-05-11
drain_slice: "#1163"
parent_slice: '#1162'
rule: file-length
rules:
  - file-length
sites: see tools/file_exemptions.txt
fix_class: hard
---

# file-exemptions

## Pattern

The file-length quality gate rejects any `src/**/*.py` file exceeding 300 lines. Ten files currently exceed this threshold and are listed in `tools/file_exemptions.txt`. Each entry is tagged `DEBT:file-exemptions` with a short description referencing the issue that caused the growth.

These files are exempt because their current size reflects a transitional state: a feature or refactor added complexity (new methods, error handling, migration steps) that pushes past the gate, but the planned extraction work has not yet landed. The exemption file is the gate's relief valve — it records intent (tracked issue) alongside the current violation, preventing the ratchet from blocking in-flight development while the drain work is scheduled.

This is tagged DEBT rather than POLICY because the gate threshold of 300 lines exists precisely to prevent large files from accumulating unchecked. No file is permanently "too complex to split" at the architectural level; large files are a symptom of deferred extraction. Each entry has a specific follow-up issue. The POLICY distinction would apply only if architectural review confirmed that a specific file structure is irreducibly complex (e.g., a migration script where extraction would break atomicity guarantees) — currently, none of the 10 entries meet that bar.

## Sites

See `artifacts/quality-debt-report.json` stale_references for all 10 entries. Representative sites from `tools/file_exemptions.txt`:

- `src/lyra/core/processors/stream_processor.py` — 468 lines (#1016 WorkerError extractor + #1098 Run lifecycle emission; `_handle_text_event` extraction planned in Slice 2 of #1096)
- `src/lyra/llm/drivers/nats_driver.py` — 434 lines (#1016 WorkerError population on transport failures; NatsDriverBase refactor in P3)
- `src/lyra/bootstrap/factory/wiring_helpers.py` — 410 lines (ADR-059/V10 bootstrap helper aggregator; 10 focused functions plus imports)
- `src/lyra/adapters/clipool/clipool_worker.py` — 371 lines (#1016 WorkerError population + exception classifier)
- `src/lyra/adapters/shared/_shared_streaming_emitter.py` — 362 lines (#1098 Run lifecycle skip branch; full v1+v2 isinstance ladder lands in Slice 2 of #1096)

## Drain plan

- Each exemption entry is its own drain target, tracked by its referenced issue. Drain proceeds per-file, not as a batch.
- `stream_processor.py` (#1016, #1098): extract `_handle_text_event` and the run-lifecycle emission block into `src/lyra/core/processors/text_event_handler.py` or equivalent. Coordinated with #1096 Slice 2.
- `nats_driver.py` (#1016): introduce `NatsDriverBase` in P3 and move transport-failure handling into a dedicated mixin. Reduces driver body by ~130 lines.
- `wiring_helpers.py` (ADR-059/V10): as protocol extraction (ADR-059) lands per-store, wiring functions for drained stores can migrate to store-specific wiring modules under `lyra.bootstrap.wiring/`.
- `_shared_streaming_emitter.py` (#1098): the v1+v2 isinstance ladder collapses when Slice 2 of #1096 consolidates event types; extraction then becomes straightforward.
- `clipool_worker.py` (#1016): extract exception classifier into `src/lyra/adapters/clipool/error_classifier.py`.
- `bootstrap_stores.py` (#957): the `idx_sql` DDL allowlist guard is the primary blocker; once the DDL validation approach stabilises, the guard can move to a dedicated validator module.
- `cli_pool.py` (#1008): `resume_direct()` is a candidate for extraction to `cli_pool_resume.py` once the session-resolution API is stable.
- `simple_agent.py` (#1008): NATS-mode `cli_nats_driver` wiring and `link_lyra_session` are candidates for a dedicated `simple_agent_nats.py` mixin.
- `adapter_standalone.py` (#1016): WorkerError startup version log is a minor addition; extraction may not be warranted — review at P2a drain pass. Candidate for permanent POLICY if review confirms atomicity requirement.
- `cli_streaming_parser.py` (#1100): ToolCall streamed-event handling (Slice 5 / #1102) will consolidate the parsing path; extraction deferred until Slice 5 lands.
- After each file is reduced below 300 lines, remove its entry from `tools/file_exemptions.txt`. When all 10 entries are drained, flip this registry to `status: drained`.

## Notes

- Issues referenced: #957, #1008, #1016, #1096, #1098, #1100, #1102. Each is the direct tracking issue for the feature or refactor that caused the file to grow past the gate.
- ADR-059 V10: covers the wiring_helpers aggregator; drain of that entry is coupled to protocol extraction progress.
- The 300-line threshold is set in `pyproject.toml` (via `stack.yml` → `quality_gates.file_length.max_lines`) and enforced by `tools/check_file_length.sh` (or equivalent). Exemptions bypass this check for listed paths only.
- `tools/file_exemptions.txt` format: `<path>  # <N> lines — DEBT:file-exemptions — <desc>`. The line count in the comment is informational (recorded at time of exemption); the actual gate runs `wc -l` at check time.
- Lifecycle: entries graduate individually. The slug drains only when `tools/file_exemptions.txt` is empty (all entries removed). Some entries (adapter_standalone.py) may be reclassified to POLICY if extraction proves structurally uneconomic — requires explicit review at that point.
