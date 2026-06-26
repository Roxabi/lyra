# Test Quality Audit — T01: tests/core/**/*.py

**Date:** 2026-05-27 | **Partition:** T01-core | **Auditor:** Claude Code

## Summary

- **Strong structural coverage (88% on src/lyra/core)** but parametrization is nearly absent (2 uses across 1,655 tests), inflating file count and maintenance surface.
- **Flaky timing patterns are endemic:** 48 `asyncio.sleep` calls across 19 files, with 20+ in the outbound-dispatcher family alone; several tests mock `asyncio.sleep` rather than injecting a clock.
- **Mock overuse concentrated in Hub/audio tests:** `test_hub_tts_dispatch.py` mocks methods *on* the SUT (Hub), and `test_outbound_dispatcher_coverage.py` patches `asyncio.sleep` + `try_notify_user` rather than using injected fakes.

---

## Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| tests/core/test_hub_tts_dispatch.py | 57, 74, 92, 109, 131, 195, 279 | High | Mocks SUT methods (`hub.dispatch_audio`, `hub.dispatch_response`) instead of verifying real behavior through injected adapter/transport fakes. | Replace with adapter-level assertions or inject a fake dispatcher; do not mock the unit under test. |
| tests/core/test_outbound_dispatcher_coverage.py | 229, 239, 361 | High | Patches `asyncio.sleep` globally and `try_notify_user` to speed up retry/backoff tests. | Inject a `sleep` callable / clock into `OutboundDispatcher`; avoid monkey-patching the standard library. |
| tests/core/test_outbound_dispatcher_coverage.py | 50, 87, 109, 135, 161, 200, 269, 295, 320, 361, 405 | High | 11 `asyncio.sleep` calls for race-condition waiting; no deterministic synchronization (events/semaphores) used. | Refactor to `asyncio.Event`, `asyncio.Queue`, or inject a test-controlled clock. |
| tests/core/test_outbound_dispatcher_media.py | 51, 71, 89, 116, 136, 154, 184, 218, 245, 277, 314, 348 | Medium | 12 `asyncio.sleep(0.05)` calls in async dispatcher media tests. | Consolidate into a shared helper that waits on `dispatcher.qsize() == 0` or use `asyncio.Event`. |
| tests/core/test_outbound_dispatcher_concurrent.py | 50, 96, 180, 189 | Medium | Sleep-based waiting in concurrent-dispatch tests; one comment explicitly says "let the worker dequeue". | Replace with `asyncio.Event` signaled from a patched `adapter.send`. |
| tests/core/test_turn_store.py | 121, 320, 429, 454 | Medium | `asyncio.sleep(0.01)` to force distinct timestamps in SQLite tests. | Use `freezegun` or inject a time provider; SQLite `datetime('now')` resolution is system-dependent. |
| tests/core/test_pool_manager.py | 233, 252 | Medium | `asyncio.sleep(0.05)` for fire-and-forget `flush_session` task completion. | Use `asyncio.Event` set inside a fake agent's `flush_session`. |
| tests/core/test_debouncer_collect.py | 89 | Medium | `asyncio.sleep(0.15)` to wait out debounce window. | Inject a test clock or use `asyncio.Event` triggered by a background task. |
| tests/core/test_hub_circuit_streaming.py | 70, 133, 196, 278 | Medium | Sleep-based waiting in streaming circuit-breaker tests. | Replace with event-driven synchronization from mocked adapter callbacks. |
| tests/core/test_config_dataclasses.py | 11-102 | Low | 3 classes (HubConfig, PoolConfig, RouterConfig) each repeat `test_default_values`, `test_custom_values`, `test_frozen_immutability` — 9 near-identical tests. | Parametrize by dataclass type and expected defaults / overrides. |
| tests/core/test_events.py | 25-165 | Low | 3 event classes (TextLlmEvent, ToolUseLlmEvent, ResultLlmEvent) each repeat `test_construction`, `test_frozen`, `test_equality`. | Parametrize by event class and field specs; fold into 1-2 parametrized tests. |
| tests/core/test_command_router_special.py | 180-237 | Low | Bare-URL detection tests (`http` vs `https`) are near-identical bodies. | Parametrize by URL scheme. |
| tests/core/test_hub_tts_dispatch.py | 184-311 | Low | 4 pref-resolution tests share identical mock-setup boilerplate (mock_tts, prefs_store, hub, msg). | Extract a fixture; parametrize by `(tts_language, msg_language, expected_language)`. |
| tests/core (aggregate) | — | Low | 153 of ~1,655 test names (~9%) do not follow `test_<unit>_<condition>_<expected>` convention. | Bulk-rename in dedicated PR; examples: `test_defaults` -> `test_hub_config_defaults_match`, `test_frozen` -> `test_hub_config_frozen_rejects_mutation`. |
| tests/core (aggregate) | — | Low | 719 MagicMock/AsyncMock instantiations across 130 files. | Audit top-5 mock-heavy files for SUT-mocking; prefer protocol fakes over `MagicMock(spec=X)`. |
| src/lyra/core/pool/pool_processor_exec.py | — | Info | 9% coverage — core pool execution path untested by T01. | Add tests in T01 or confirm coverage by integration suite elsewhere. |
| src/lyra/core/pool/pool_processor.py | — | Info | 11% coverage — pool scheduling/processor orchestration largely untested. | Same as above. |
| src/lyra/core/hub/outbound/outbound_streaming.py | — | Info | 14% coverage — streaming dispatch path untested. | Add streaming-dispatch tests or verify covered by outbound/integration suite. |
| src/lyra/core/hub/outbound/outbound_tts.py | — | Info | 16% coverage — TTS outbound path untested. | Add dedicated tests or confirm covered by hub TTS dispatch tests. |
| src/lyra/core/stream_processor.py | — | Info | 16% coverage — stream processing engine untested in core. | Confirm coverage by CLI-pool or adapter tests; backfill if not. |
| src/lyra/core/memory/*.py | — | Info | 16-19% coverage — memory subsystem (freshness, schema, upserts) lightly touched. | Review if memory tests belong in T01 or a dedicated memory partition. |
| src/lyra/core/runtime_config.py | — | Info | 18% coverage — runtime config overlays / persistence untested. | Backfill tests for `save`, `load`, `overlay` logic. |

---

## Metrics

| Metric | Value |
|--------|-------|
| Test files | 130 |
| Test functions | 1,655 |
| Pass rate | 100% (1,655 passed, 4 skipped) |
| `src/lyra/core` line coverage | **88%** |
| `src/lyra/core` modules < 20% coverage | 6 (pool_processor_exec, pool_processor, outbound_streaming, outbound_tts, stream_processor, tts_dispatch) |
| `@pytest.mark.parametrize` usages | **2** (0.12% of test functions) |
| `asyncio.sleep` / `time.sleep` calls in tests | **48** across 19 files |
| `MagicMock` / `AsyncMock` instantiations | **719** across 130 files |
| Test names violating `test_<unit>_<condition>_<expected>` | **153** (~9%) |
| Files with > 20 mocks | 5 (test_hub_tts_dispatch, test_command_router_special, test_outbound_dispatcher_coverage, test_routing_context_integration, test_outbound_dispatcher_media) |
| Files with > 5 sleep calls | 7 |

---

## Recommendations (Prioritized)

1. **Eliminate SUT mocking in Hub audio tests** (P1 — High)
   - In `test_hub_tts_dispatch.py`, `hub.dispatch_audio = AsyncMock()` mocks the system under test. Replace with a fake `ChannelAdapter` that records calls, or assert on the outbound queue. This is the most egregious mock-overuse instance in T01.

2. **Inject a test clock / replace sleep patches in OutboundDispatcher tests** (P1 — High)
   - `test_outbound_dispatcher_coverage.py` patches `asyncio.sleep` and `try_notify_user`. Add an optional `sleep: Callable[[float], Awaitable[None]]` parameter to `OutboundDispatcher.__init__` (default `asyncio.sleep`), then pass `AsyncMock()` or `noop` in tests. Similarly inject a `notify_user` callable. Remove all `patch("asyncio.sleep", ...)` occurrences.

3. **Parametrize dataclass & event boilerplate** (P2 — Medium)
   - Convert `test_config_dataclasses.py` and `test_events.py` to parametrized tables. This alone removes ~15 redundant test bodies and demonstrates the pattern for the rest of the suite.

4. **Standardize async synchronization: no raw sleeps > 0.01s** (P2 — Medium)
   - Target the 7 files with > 5 sleep calls (outbound dispatcher family, hub circuit, pool manager). Replace with `asyncio.Event` or queue-drain helpers. Add a project lint rule (or a simple grep in CI) that blocks new `asyncio.sleep` in tests without a `# noqa: sleep-justified` comment.

5. **Backfill coverage for 6 sub-20% modules** (P3 — Low)
   - `pool_processor_exec.py`, `pool_processor.py`, `outbound_streaming.py`, `outbound_tts.py`, `stream_processor.py`, `tts_dispatch.py` — verify whether these are covered by integration/adapter tests outside T01. If not, add targeted unit tests or document the coverage gap in `tests/README.md` with the reason (e.g., "covered by adapter streaming suite").
