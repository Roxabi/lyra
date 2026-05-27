### Summary
- 31% src/lyra coverage from integration suite; 12 of 112 tests skipped (mostly infra-dependent NATS/voice stubs).
- 6 files contain sleep-based or timeout-catch flaky patterns; 4 files mock private attributes of the module under test.
- Strong duplication between `test_session_telegram.py` and `test_session_dm_discord.py` (near-identical test shape) and repeated stub boilerplate in `test_command_sessions.py`.

### Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `test_e2e_telegram_to_agent.py` | 128-131, 156-159 | High | `asyncio.wait_for(hub.run(), timeout=1.0)` with swallowed `TimeoutError` — hub.run() loops forever; timeout is load-sensitive and arbitrary. | Replace timeout-and-swallow with a deterministic synchronization primitive (e.g., `asyncio.Event` set by the adapter spy). |
| `test_message_pipeline.py` | 291-294 | High | `asyncio.wait_for(hub.run(), timeout=0.3)` with swallowed `TimeoutError` — same pattern as above, tighter timeout. | Use explicit event/callback spy instead of time-based heuristic. |
| `test_session_reply_to.py` | 196, 226 | High | `await asyncio.sleep(0.1)` to wait for `pool.submit()` side effects — race-prone; 100 ms may be insufficient on loaded CI. | Replace with `asyncio.Event` wired into the pool or use `pytest-asyncio` `timeout` with deterministic task completion. |
| `test_voice_routing.py` | 84-94, 155, 164-169, 252, 297-303 | Medium | Multiple `await asyncio.sleep(0.5)` and `time.sleep(1)` loops for Docker/NATS readiness polling; worst-case ~40 s of sleeps. | Use exponential-backoff helper fixture with health-check callback; cap total wait and fail fast. |
| `test_crash_recovery.py` | 244-245, 259 | Medium | Monkey-patches `writer._handle_increment_resume_count` (private method of module under test) to simulate crash. | Inject crash strategy via constructor param or use a subclass instead of runtime method replacement. |
| `test_session_reply_to.py` | 113 | Medium | Directly assigns `pool._current_task` (private attr) to force busy state. | Expose a test-only helper on `Pool` or use a factory fixture that returns a pre-wired pool. |
| `test_session_telegram.py` | 126-128, 133-134 | Medium | Mocks private attrs `adapter.bot`, `adapter._start_typing`, `adapter._cancel_typing` on module under test. | Use a test double subclass of `TelegramAdapter` that overrides these methods instead of monkey-patching. |
| `test_session_dm_discord.py` | 108 | Medium | Directly sets `adapter._bot_user` (private attr). | Pass bot_user into adapter constructor or use a test-double subclass. |
| `test_command_sessions.py` | 69-123 | Low | Three almost identical stub functions (`_stub_vault_add`, `_stub_explain`, `_stub_summarize`) differ only in command name and response template. | Parameterize with a single `_stub_url_command(name, template)` factory. |
| `test_session_telegram.py` + `test_session_dm_discord.py` | module-level | Low | Near-identical test structure: fake turn store, fake message, adapter construction, bus spy, assertion on `thread_session_id`. | Extract a parameterized base test or shared fixture module for cross-adapter injection behavior. |
| `test_voice_end_to_end.py` | 41-47 | Low | Entire module skipped at top-level (`pytestmark = pytest.mark.skip(...)`). 3 tests are dead code until V2 voice refactor lands. | Keep skip but add issue link in skip reason (#1067/#1308) and schedule re-enable in backlog. |
| `test_worker_error_e2e.py` | 91 | Low | Single skipped test with v1 stub classes (`TextRenderEvent`, `ToolSummaryRenderEvent`) typed as `Any` — pyright suppression required. | Remove v1 stubs once #1192 S3 ships; add TODO with issue reference. |
| `test_reasoning_e2e.py` | 129 | Low | `test_full_pipeline_emits_reasoning_then_text` skipped — v1 removed in #1192 S3. | Schedule re-enable when v2 reasoning events land. |
| `test_crash_recovery.py` | 295-317, 325-372 | Low | Two tests (`test_jetstream_redelivery_on_no_ack`, `test_lsof_sole_writer`) are unconditional `pytest.skip(...)` with dead-code scaffolds. | Convert to conditional skip based on env / fixture availability, or move to deploy-validation suite. |
| `test_command_sessions.py` | 180, 215, 239, etc. | Low | Several method names missing `<expected>` segment of `test_<unit>_<condition>_<expected>` convention. | Rename: e.g., `test_bare_url_dispatched_to_add_returns_response`, `test_happy_path_submit_to_pool_returns_submitted`. |
| `integrations/test_audio_converter.py` | 22, 35, etc. | Low | `test_happy_path_completes` lacks `<expected>` segment. | Rename to `test_happy_path_completes_without_error`. |
| `integrations/test_systemctl.py` | 21, 41, etc. | Low | `test_status_all_invokes_every_unit` OK, but `test_happy_path_completes` pattern repeats across audio/supervisor/vault/web_intel. | Standardize naming; consider shared `CliWrapperTestBase` for the four subprocess-mock integration suites. |
| `test_message_pipeline.py` | 74 | Low | `test_happy_path_submit_to_pool` missing `<expected>` component. | Rename to `test_happy_path_submit_to_pool_returns_submitted`. |
| `test_voice_routing.py` | 245 | Medium | `TestLoadAwareRoutingLocal` skipped in CI via `pytest.mark.skipif(os.getenv("CI") == "true", ...)` — local-only tests rot silently. | Run in a nightly/weekly job or convert to containerized test that scales workers programmatically. |

### Metrics

| Metric | Value |
|--------|-------|
| Total tests collected | 112 |
| Tests passed (last run) | 100 |
| Tests skipped | 12 (~11%) |
| src/lyra coverage (integration suite only) | 31% |
| Files with flaky sleeps/timeouts | 6 |
| Files mocking private attrs of SUT | 4 |
| Files with naming `<expected>` gaps | 7 |
| Parametrize / deduplicate opportunities | 3 clusters |
| Skipped placeholder tests (dead code) | 5 |

### Recommendations

1. **Eliminate timeout-and-swallow patterns** — `test_e2e_telegram_to_agent.py` and `test_message_pipeline.py` both rely on `asyncio.wait_for(..., timeout=N)` + swallowed `TimeoutError` to stop an infinite loop. Replace with deterministic synchronization (adapter spy sets an `asyncio.Event`) so the test is load-independent and fails fast on real hangs.

2. **Replace sleep-based polling with event-driven assertions** — `test_session_reply_to.py` uses `asyncio.sleep(0.1)` to wait for side effects of `pool.submit()`. Use `pytest-asyncio` primitives or expose a test-only completion future on `Pool` to remove the race window.

3. **Stop mocking private attributes of the module under test** — `test_session_telegram.py`, `test_session_dm_discord.py`, and `test_session_reply_to.py` set private attrs (`_bot_user`, `_start_typing`, `_current_task`). Introduce test-double subclasses or constructor injection points so integration tests exercise public surface area only.

4. **Consolidate duplicated cross-adapter session-injection tests** — `test_session_telegram.py` and `test_session_dm_discord.py` share ~80% of their structure. Extract a shared parameterized fixture or base class so the same assertions run for both adapters without copy-paste drift.

5. **Schedule cleanup of skipped placeholder tests** — 5 tests are unconditional `pytest.skip` with dead-code scaffolds (`test_worker_error_e2e.py`, `test_reasoning_e2e.py` v1 test, `test_voice_end_to_end.py` module skip, and 2 in `test_crash_recovery.py`). Either wire them to real implementations or move them to a `tests/deploy_validation/` directory so they do not clutter CI collection and coverage reports.
