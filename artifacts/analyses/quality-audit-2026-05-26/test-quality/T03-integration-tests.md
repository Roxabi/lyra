# T03 Integration Tests — Quality Audit

**Date:** 2026-05-27
**Partition:** `tests/integration/**/*.py`
**Context:** Epic #1277 stage-axis refactor active; prior audit (2026-05-18) covered hexagonal conformance, mutualization, simplification. This audit focuses on test quality only.

---

### Summary

- **Coverage at 31%** (16,866 statements, 10,895 missed) — expected for integration-only, but large blind spots in adapters, bootstrap standalone, and blobstore (0-20%).
- **Zero `@pytest.mark.parametrize`** across 112 test functions in 22 files; integrations/ subdirectory repeats identical error-path test bodies (FileNotFound, nonzero-exit, timeout) 5 times without parametrization.
- **Flaky polling loops** in `test_voice_routing.py` (hard-coded `time.sleep(1)` / `asyncio.sleep(0.5)` against Docker Compose health) and `test_session_reply_to.py` (`asyncio.sleep(0.1)` waiting for `create_task`); no `asyncio.Event`, `Condition`, or `pytest-asyncio` timeout fixtures used for synchronization.

---

### Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `tests/integration/test_voice_routing.py` | 84-94 | **High** | Polling loop with `time.sleep(1)` up to 30 s waiting for Docker Compose health; no retry backoff or early-break on failure. | Replace with `asyncio.wait_for` around an `asyncio.Event` signaled by a health-check callback, or use `docker compose wait` CLI. |
| `tests/integration/test_voice_routing.py` | 155, 165, 252, 298 | **High** | Heartbeat-wait loops use `await asyncio.sleep(0.5)` up to 10 s without proper synchronization primitive. | Use `asyncio.Event` + `wait_for` with a callback on the NATS subscription; cap total wait with a single `wait_for` rather than looped sleeps. |
| `tests/integration/test_session_reply_to.py` | 196, 226 | **Medium** | `await asyncio.sleep(0.1)` used to wait for `pool.submit()`-spawned task to reach resume step; timing-dependent on event-loop scheduling. | Replace sleep with an `asyncio.Event` set inside the resume callback and awaited via `asyncio.wait_for`. |
| `tests/integration/integrations/test_supervisor.py` | 22-99 | **Medium** | 6 test functions (`test_status_all_returns_stdout`, `test_file_not_found_raises`, `test_nonzero_exit_raises`, `test_timeout_raises`, `test_restart_service_calls_correct_args`, `test_status_all_does_not_append_service`) share identical `create_subprocess_exec` patching pattern but are duplicated verbatim. | Extract a parametrized fixture or `@pytest.mark.parametrize` over `(action, service, returncode, side_effect, expected_reason)`. |
| `tests/integration/integrations/test_systemctl.py` | 21-151 | **Medium** | Same pattern as supervisor: 10 tests duplicate `patch("asyncio.create_subprocess_exec", ...)` scaffolding for every error path. | Parametrize over `(returncode, stdout, stderr, side_effect, expected_reason)`; share a single `_make_proc` fixture already exists but is file-local and not reused across files. |
| `tests/integration/integrations/test_vault_cli.py` | 28-134 | **Medium** | 11 tests repeat `patch("asyncio.create_subprocess_exec", ...)` pattern; `_make_proc` helper is file-local duplicate of the one in `test_systemctl.py` and `test_web_intel.py`. | Move `_make_proc` to `tests/integration/conftest.py` and parametrize happy-path / error-path rows for `add` and `search`. |
| `tests/integration/integrations/test_web_intel.py` | 27-114 | **Medium** | 10 tests repeat identical `create_subprocess_exec` + JSON payload pattern. `_make_proc` duplicated again. | Same as vault_cli: shared fixture + parametrization. |
| `tests/integration/integrations/test_audio_converter.py` | 22-99 | **Medium** | 5 tests duplicate `AsyncMock(return_value=proc)` / `side_effect=FileNotFoundError` / `side_effect=TimeoutError` pattern. | Parametrize over `(side_effect, returncode, expected_reason)`. |
| `tests/integration/test_crash_recovery.py` | 244-245, 259 | **Medium** | Patches `writer._handle_increment_resume_count` directly — mocks the module under test's internal handler. | Inject the crash via a test-specific `Writer` subclass or factory parameter rather than monkey-patching a private method. |
| `tests/integration/test_message_pipeline.py` | 290 | **Medium** | `patch.object(pool, "submit", side_effect=_spy_submit)` — patches core behavior of the object under test rather than observing it. | Spy via an explicit `RecordingPool` subclass or a `pool.submit` wrapper set in fixture; avoid `patch.object` on internals. |
| `tests/integration/test_session_telegram.py` | 133-134, 179-180 | **Low** | Patches `adapter._start_typing` and `adapter._cancel_typing` private methods to no-ops. | Pass a `typing_behavior=None` config or construct adapter with a no-op typing strategy rather than patching internals. |
| `tests/integration/test_reasoning_e2e.py` | 129 | **Low** | `@pytest.mark.skip(reason="v1 removed in #1192 S3 — rewrite for v2 deferred")` — skipped for >2 months with deferred rewrite. | Schedule issue to re-enable or delete the skeleton; skipped tests rot silently. |
| `tests/integration/test_worker_error_e2e.py` | 92 | **Low** | `@pytest.mark.skip(reason="v1 removed in #1192 S3 — rewrite for v2 deferred")` — same as above. | Same: schedule re-enable or delete; the file is 207 lines of dead code. |
| `tests/integration/test_voice_end_to_end.py` | 41-47 | **Low** | `@pytest.mark.skip(reason="V2 wire refactor (#1064) ...")` — skipped pending upstream wiring; acceptable skip but has no expiry/re-enable tracking issue. | Add a `# TODO(#ISSUE)` comment linking to the re-enable tracking issue so skip reason is actionable. |
| `tests/integration/test_message_pipeline.py` | — | **Low** | `RuntimeWarning: coroutine 'AsyncMockMixin._execute_mock_call' was never awaited` emitted during test run for `test_no_binding_drops_with_trace` and `test_trace_hook_is_optional`. | Fix the mock setup: an `AsyncMock` is assigned to a sync callable path or vice versa; check `trace_hook` wiring. |

---

### Metrics

| Metric | Value |
|--------|-------|
| Total test files | 22 |
| Total test functions | 112 |
| Tests run (112 items) | 112 (with skips) |
| Skipped tests (collected but not run) | 3 |
| Skipped by environment (nats-server, lsof, docker) | 3 |
| Files using `MagicMock` / `AsyncMock` | 16 of 22 (73%) |
| Files using `patch` | 14 of 22 (64%) |
| `@pytest.mark.parametrize` usages | **0** |
| `asyncio.sleep` / `time.sleep` calls | 7 |
| Coverage % (src/lyra, integration tests only) | **31%** |
| Coverage % — adapters (discord, telegram, clipool) | 0-54% |
| Coverage % — bootstrap standalone | 0-24% |
| Coverage % — blobstore | 0% |
| Coverage % — outbound / streaming | ~15-30% |

---

### Recommendations (Prioritized)

1. **Eliminate sleep-based polling in `test_voice_routing.py` and `test_session_reply_to.py`.** Replace all `time.sleep(1)` / `asyncio.sleep(0.5)` / `asyncio.sleep(0.1)` loops with `asyncio.Event` + `asyncio.wait_for`, or with `pytest-asyncio` timeout fixtures. This is the highest flakiness risk in the partition.

2. **Parametrize the five `integrations/` error-path test matrices.** `test_supervisor.py`, `test_systemctl.py`, `test_vault_cli.py`, `test_web_intel.py`, and `test_audio_converter.py` each contain 5-10 near-identical test functions for `(FileNotFound, nonzero_exit, timeout, happy_path)`. Extract shared `_make_proc` fixture to `tests/integration/conftest.py` and apply `@pytest.mark.parametrize` to collapse ~35 test functions into ~5 parametrized functions. Estimated reduction: 30% line count in `integrations/`.

3. **Stop patching module-under-test internals.** Replace `patch.object(pool, "submit", ...)` in `test_message_pipeline.py`, `writer._handle_increment_resume_count = ...` in `test_crash_recovery.py`, and `adapter._start_typing = MagicMock()` in `test_session_telegram.py` with constructor-level injection of test doubles or subclass overrides. Patching private methods creates brittle tests that break during #1277 refactors.

4. **Delete or schedule re-enable for permanently skipped skeleton tests.** `test_reasoning_e2e.py` and `test_worker_error_e2e.py` are each >120 lines of skipped code referencing "v1 removed in #1192 S3 — rewrite for v2 deferred". If the rewrite is >2 months deferred, delete the skeletons and reopen as tracked issues. For `test_voice_end_to_end.py`, add a concrete tracking issue number to the skip reason.

5. **Add a `tests/integration/conftest.py` with shared fixtures.** Currently each integration test file duplicates `_make_pool()`, `_make_inbound_message()`, `_make_hub()`, `_FakeTurnStore`, `_make_proc`, and `make_mock_driver()`. Centralizing these into a single `conftest.py` reduces duplication and makes the integration suite easier to maintain during the stage-axis refactor.
