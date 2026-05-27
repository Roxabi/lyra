# Test Quality Audit — T04 (nats / transport / llm / streaming)

**Date:** 2026-05-27
**Scope:** `tests/nats/**/*.py`, `tests/transport/**/*.py`, `tests/llm/**/*.py`, `tests/streaming/**/*.py`
**Prior audit:** 2026-05-18 (hexagonal conformance, duplication, dead-code) — findings not regressed.
**Tests run:** 468 passed, 0 failed, 0 skipped (xdist 24 workers)

---

## Summary

- **Coverage:** 78% overall; streaming at 98%, but nats (75%) and llm (75%) have large codec dead zones (`nats_tts_codec` 0%, `cli_pool_codec` 0%).
- **Flaky patterns:** 9 unmocked `asyncio.sleep` calls in integration tests (0.05–0.5 s) used for NATS delivery / Hub startup synchronization — race-condition surface.
- **Mock overuse:** 45 bare `MagicMock()` calls, only 6 with `spec=`; three NATS codec modules (`tts_codec`, `stt_codec`, `image_codec`) are mocked in client tests instead of being exercised directly.
- **Parametrize gaps:** TurnPublisher method tests, NatsChannelProxy event-type tests, and result-type frozen tests repeat identical bodies and could collapse to parametrized cases.

---

## Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|---------------|----------------|
| `tests/nats/conftest.py` | 52–58 | Medium | `time.sleep(0.05)` polling loop waits for nats-server startup with hardcoded 5 s deadline and 50 ms step. | Replace with `asyncio.Event` or `socket` readiness check with exponential back-off; reduce CI flakiness on slow runners. |
| `tests/nats/test_nats_bus.py` | 426 | Medium | `await asyncio.sleep(0.1)` in `test_staging_qsize` — assumes NATS delivery completes in 100 ms. | Use `asyncio.wait_for` on an `asyncio.Event` fired by subscriber handler, or `nc.flush()` + `asyncio.Event`. |
| `tests/nats/test_nats_bus.py` | 521 | Medium | `await asyncio.sleep(0.5)` in queue-group distribution test — 500 ms is a long unbounded wait. | Drain via `asyncio.Event` set when expected message count received; cap with `wait_for`. |
| `tests/nats/test_nats_bus_multibot.py` | 113 | Medium | `await asyncio.sleep(0.15)` for NATS delivery before staging_qsize assertion. | Same as above — event-driven synchronization instead of blind sleep. |
| `tests/nats/test_hub_standalone.py` | 265, 276, 355, 366 | High | Four `await asyncio.sleep(0.05)` calls + one `0.1` (line 369) used to "give Hub.run() a moment to enter its get() await / finish pipeline processing." | This is a documented race condition. Replace with `asyncio.Event` signaled when Hub reaches the await point, or mock the pipeline stage. |
| `tests/nats/test_keepalive.py` | 125 | Low | `await asyncio.sleep(5 * fast_interval)` where `fast_interval` is monkeypatched to 50 ms — sleep is bounded and controlled, but still sleep-based. | Acceptable given the keepalive interval is the SUT; no action needed unless interval increases. |
| `tests/nats/test_sanitize_single_entry.py` | 42–46 | Medium | `EXPECTED_CALL_SITES` is empty because `sanitize_platform_meta` was removed from inbound path (#853). Test now passes vacuously — it greps source and asserts zero call sites match zero expectations. | Update test contract comment or delete file; a zombie inventory test adds no value once the architecture it guards is gone. |
| `src/lyra/nats/nats_tts_codec.py` | — | High | **0% coverage** (38 stmts, 38 miss). The entire module is untested. | Add unit tests for `NatsTtsCodec.encode/decode` analogous to `test_llm_codec.py`. |
| `src/lyra/llm/cli_pool_codec.py` | — | High | **0% coverage** (59 stmts, 59 miss). No tests exist in T04 or elsewhere. | Add codec tests; this is a sibling of `cli_nats_codec` which is at 98%. |
| `src/lyra/nats/tts_engine_selector.py` | — | High | **0% coverage** (29 stmts, 29 miss). | Add engine-selection logic tests. |
| `src/lyra/nats/tts_text_normalization.py` | — | High | **0% coverage** (20 stmts, 20 miss). | Add normalization unit tests. |
| `src/lyra/nats/nats_stt_codec.py` | — | Medium | **55% coverage** (34 stmts, 13 miss). Partially exercised only via mocked client tests. | Add direct codec tests for error-path branches (lines 50–68, 72–97). |
| `src/lyra/nats/nats_image_codec.py` | — | Medium | **63% coverage** (47 stmts, 15 miss). | Add direct codec tests for blob_ref validation and error branches (lines 67–95, 99–109). |
| `src/lyra/transport/worker_pool_client.py` | — | Medium | **64% coverage** (86 stmts, 31 miss). Untested: fallback worker logic, retry loop, drain, timeout branches. | Add tests for `request_with_routing` fallback paths, `stream_request` early-break, and `stop` behavior. |
| `src/lyra/llm/decorators.py` | — | Low | **86% coverage** (58 stmts, 6 miss). Missing decorator property accessors and edge cases. | Add tests for lines 93–96, 99, 157–160, 163. |
| `tests/nats/test_nats_tts_client.py` | 22–26 | Medium | Client test mocks the codec (`_make_codec`) instead of using real `NatsTtsCodec`. This hides codec regressions. | Split: keep client tests with real codec + mocked transport; add separate codec unit tests. |
| `tests/nats/test_nats_image_client.py` | 71–74 | Medium | Same pattern: `codec = MagicMock()` hides `NatsImageCodec` behavior. | Same recommendation as TTS client. |
| `tests/nats/test_nats_stt_client.py` | 22–26 | Medium | Same pattern: `codec = MagicMock()` hides `NatsSttCodec` behavior. | Same recommendation as TTS client. |
| `tests/transport/test_turn_publisher.py` | 58–268 | Low | Five `TestPublish*` classes repeat almost identical "subject == lyra.turns.write" and "kind == X" assertions. | Collapse to a single `@pytest.mark.parametrize` over `(method_name, expected_kind, extra_assertions)`. |
| `tests/nats/test_nats_channel_proxy.py` | 260–291 | Low | `test_send_streaming_event_type_text` and `test_send_streaming_event_type_tool_call_start` have identical body shape (publish, decode, assert event_type). | Parametrize over event class + expected event_type string. |
| `tests/transport/test_result_types.py` | 26–35 | Low | `test_ok_frozen` and `test_err_frozen` are structurally identical; could be parametrized over `(Ok, Err)`. | Use `@pytest.mark.parametrize("cls", [Ok, Err])` with a single frozen test body. |
| `tests/llm/test_decorators.py` | 93–179 | Low | RetryDecorator tests patch `asyncio.sleep` with `AsyncMock` 5 times — repeated boilerplate. | Extract a shared fixture or helper that patches sleep for the test class. |
| `tests/nats/test_hub_standalone.py` | 396–508 | Low | `test_bootstrap_hub_standalone_passes_identity_name` and `test_bootstrap_adapter_standalone_passes_platform_identity_name` share ~80% setup code (mock nc, mock nats_connect, sentinel exit). | Extract a shared `_bootstrap_with_sentinel()` helper to reduce duplication. |

---

## Metrics

| Partition | Tests | Source Stmts | Miss | Cover % | Missing modules (cover < 80%) |
|-----------|-------|--------------|------|---------|------------------------------|
| `tests/nats` | 244 | 636 | 141 | **75%** | `nats_tts_codec` 0%, `tts_engine_selector` 0%, `tts_text_normalization` 0%, `nats_stt_codec` 55%, `nats_image_codec` 63%, `stt_helpers` 83% |
| `tests/transport` | 109 | 275 | 35 | **87%** | `worker_pool_client` 64% |
| `tests/llm` | 75 | 314 | 74 | **75%** | `cli_pool_codec` 0%, `decorators` 86%, `drivers/cli` 85% |
| `tests/streaming` | 40 | 53 | 1 | **98%** | `state_machine` 97% (line 55) |
| **T04 Total** | **468** | **1278** | **251** | **78%** | — |

### Mock usage counts

| Pattern | Count |
|---------|-------|
| `MagicMock()` (bare, no spec) | 45 |
| `AsyncMock()` (bare) | 35 |
| `MagicMock(spec=...)` | 6 |
| `AsyncMock(return_value=...)` | ~20 |

### Timing / flaky-surface counts

| Pattern | Count | Files |
|---------|-------|-------|
| `asyncio.sleep` in test body | 8 | `test_keepalive.py`, `test_nats_bus.py`, `test_nats_bus_multibot.py`, `test_hub_standalone.py` |
| `time.sleep` in fixture | 1 | `tests/nats/conftest.py` |
| `asyncio.sleep` mocked via patch | 6 | `test_decorators.py` |

### Parametrize usage

| Partition | `@pytest.mark.parametrize` occurrences |
|-----------|----------------------------------------|
| nats | 5 |
| transport | 2 |
| llm | 1 |
| streaming | 1 |

---

## Recommendations (prioritized, max 5)

1. **Eliminate unmocked `asyncio.sleep` in integration tests (High)**
   Replace 8 real sleeps in `test_nats_bus.py`, `test_nats_bus_multibot.py`, `test_hub_standalone.py` with `asyncio.Event` synchronization. The `test_hub_standalone.py` sleeps (0.05–0.1 s) are the most fragile because they gate on internal Hub state transitions.

2. **Add codec tests for zero-coverage modules (High)**
   `nats_tts_codec.py`, `cli_pool_codec.py`, `tts_engine_selector.py`, and `tts_text_normalization.py` are completely untested. Write pure unit tests (no NATS, no transport) analogous to `test_llm_codec.py`.

3. **Stop mocking the codec in NATS client tests (Medium)**
   `test_nats_tts_client.py`, `test_nats_image_client.py`, and `test_nats_stt_client.py` use `MagicMock` for their respective codecs. Switch to real codecs + mocked transport so codec regressions are caught by client tests.

4. **Parametrize repetitive test bodies (Low)**
   Collapse TurnPublisher method tests (`test_turn_publisher.py`), NatsChannelProxy event-type tests, and result-type frozen tests into parametrized cases. This reduces ~15–20% of T04 line count without losing coverage.

5. **Add WorkerPoolClient fallback/retry branch coverage (Medium)**
   `worker_pool_client.py` is at 64%; missing lines cover fallback worker selection, retry exhaustion, and `stream_request` early-break. Add tests that force these branches with mocked `NatsTransport.call` returning timeouts and no-responders.
