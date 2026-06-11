# Changelog

## [0.10.0] (2026-06-11)

### Features

* **contracts/jobs:** unify jobs subject taxonomy — rename `factory.results.<job_id>` → `factory.job.<job_id>.result` and `factory.progress.<job_id>` → `factory.job.<job_id>.progress` ([#1793](https://github.com/Roxabi/roxabi-factory/issues/1793)). Updates `jobs_result()` and `jobs_progress()` return values and docstrings.
* **contracts/jobs:** add `jobs_steer()`, `jobs_opened()`, `jobs_closed()` subject helpers for the full `factory.job.<id>.*` subtree ([#1793](https://github.com/Roxabi/roxabi-factory/issues/1793)). Exposes `jobs_steer`, `jobs_opened`, `jobs_closed` from `roxabi_contracts.jobs`.
* **contracts/jobs:** remove retired `_Subjects.result_prefix` and `_Subjects.progress_prefix` fields; add `_Subjects.job_prefix` (`"factory.job"`).

### BREAKING CHANGES

* `jobs_result(job_id)` and `jobs_progress(job_id)` return different subject strings. Any caller pinned to `factory.results.*` or `factory.progress.*` wire subjects must update to the new `factory.job.<id>.*` pattern. Confirmed 0 in-repo callers at time of release.


## [0.9.0] (2026-06-11)

### Features

* **contracts:** add `WorkEnvelope` base class with `job_id` + `parent_job_id` — work-plane/infra-plane split ([#1619](https://github.com/Roxabi/roxabi-factory/issues/1619)). TRANSITIONAL: `job_id` carries a `default_factory` so pre-#1619 wire messages without the field still parse; flip to hard-required tracked in [#1841](https://github.com/Roxabi/roxabi-factory/issues/1841).
* **contracts:** export `new_job_id()` from top-level `roxabi_contracts` — 32-char hex UUID, NATS-subject-safe.
* **contracts:** reparent 13 domain models to `WorkEnvelope`: `JobEnvelope`, `JobResult`, `JobProgress`, `LlmRequest`, `LlmChunkEvent`, `LlmResponse`, `TtsRequest`, `TtsResponse`, `SttRequest`, `SttResponse`, `ImageRequest`, `ImageResponse`, `TurnWriteEvent`. Infra-plane models (`LifecycleRequest/Response`, `ImageHeartbeat`, `CliHeartbeat`, `BlobAuditEvent`, `SecurityEvent`, `LyraEvent/Metric`, `MintFailureEvent`) remain on `ContractEnvelope` by design. CLI models (`CliCmdPayload`, `CliChunkEvent`, `CliControlCmd`, `CliControlAck`) deferred to [#1838](https://github.com/Roxabi/roxabi-factory/issues/1838).
* **contracts:** add enforcement tests locking the WORK/INFRA/PENDING classification invariants (`tests/test_work_envelope_subjects.py`).


## [0.8.0](https://github.com/Roxabi/roxabi-factory/compare/roxabi-contracts/v0.7.0...roxabi-contracts/v0.8.0) (2026-06-11)


### Features

* **contracts:** consolidate NATS subject-segment charset SSoT in `_nats_utils` ([#1782](https://github.com/Roxabi/roxabi-factory/issues/1782)). Introduces `_SAFE_SEGMENT_CHARS` (chars-only, for sanitizer composition) and `_SAFE_SEGMENT_RE` (compiled full-match regex) as the single source of truth, removing two duplicate inline regexes across domain subjects modules. Promotes `_validate_subject_segment` → `validate_subject_segment` (public API) and re-exports it from the package root. Additive, non-security-bearing — existing callers that imported `_validate_subject_segment` by private name must update to `validate_subject_segment`.


## [0.4.0](https://github.com/Roxabi/lyra/compare/roxabi-contracts/v0.3.0...roxabi-contracts/v0.4.0) (2026-05-19)


### Features

* **contracts:** add optional `agent_name` and `agent_email` fields to `CliCmdPayload` for per-session git committer attribution ([#1150](https://github.com/Roxabi/lyra/issues/1150)). Additive, non-security-bearing — older consumers ignore the fields per the package's forward-compat policy (`extra='ignore'`).


## [0.3.0] (unreleased note backfill)

`pyproject.toml` was bumped to `0.3.0` between `0.2.0` and this PR without a corresponding CHANGELOG entry. This placeholder preserves the version chain for the `[0.4.0]` compare URL. Backfill from `git log packages/roxabi-contracts/ pyproject.toml` if/when the gap matters.


## [0.2.0](https://github.com/Roxabi/lyra/compare/roxabi-contracts/v0.1.0...roxabi-contracts/v0.2.0) (2026-04-17)


### Features

* **contracts:** port voice domain — models + subjects + fixtures ([#763](https://github.com/Roxabi/lyra/issues/763)) ([#777](https://github.com/Roxabi/lyra/issues/777)) ([c632d07](https://github.com/Roxabi/lyra/commit/c632d07b1d3aad1f7a51f51790d6d171d2e75796))
* **contracts:** scaffold packages/roxabi-contracts skeleton + envelope.py ([#771](https://github.com/Roxabi/lyra/issues/771)) ([cffec07](https://github.com/Roxabi/lyra/commit/cffec072a3315daa6cc3753f68785d3205747a01))


### Bug Fixes

* **contracts:** add reportMissingImports directive for voice fixtures ([67160a0](https://github.com/Roxabi/lyra/commit/67160a0e44273b074f6b05c8f4c66e1184f54727))
