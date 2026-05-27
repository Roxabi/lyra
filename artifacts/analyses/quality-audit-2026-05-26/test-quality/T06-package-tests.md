# Test Quality Audit — Partition T06: `packages/**/tests/**/*.py`

Date: 2026-05-26
Auditor: Claude
Scope: roxabi-contracts, roxabi-nats, roxabi-blobs test suites
Prior audit: 2026-05-18 (hexagonal conformance, mutualization, dead-code) — findings not re-reported unless regressed.

---

## Summary

- **Coverage is strong** across all three packages (79–96%), with the roxabi-nats gap concentrated in `_serialize.py` (54%) and testing doubles (82–89%).
- **No sleep-based flakiness in unit-test partitions**, but two roxabi-nats integration tests against real NATS server use hardcoded `asyncio.sleep(0.2)` as a race-condition crutch.
- **Parametrization is heavily used** (56 `@pytest.mark.parametrize` decorators) and generally well-executed, yet a few inline loops and near-duplicated test pairs remain un-parametrized.
- **Mock discipline is acceptable** — no module-under-test self-mocking detected; collaborators (NATS client, SQLite connection, `os.*`) are mocked at appropriate boundaries.

---

## Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `packages/roxabi-blobs/tests/test_protocol_parametrized.py` | 17 | **High** | Top-level import `from roxabi_blobs.http_store import HttpBlobStore` targets a module that does not exist (RED phase). This causes **collection failure** for the entire file — the FsBlobStore variant can never run. | Defer import to fixture body (already partially done) or mark the file `pytestmark = pytest.mark.skip(reason="T8 not landed")` until `HttpBlobStore` exists. |
| `packages/roxabi-nats/tests/test_readiness.py` | 368, 466 | **Medium** | `await asyncio.sleep(0.2)` used as a timing guess in KV watch race tests (`test_kv_watch_returns_true_after_key_written`, `test_adapter_logs_watch`). The 200 ms delay is meant to let the probe reach the watch path before `announce_hub_ready` writes the key, but on slow CI runners the probe may still miss the window. | Replace with an explicit synchronization primitive (e.g., `asyncio.Event` set by a monkey-patched `_open_kv_with_retry` or by polling the probe task state) so the test is deterministic. |
| `packages/roxabi-nats/tests/test_voice_testing_doubles.py` | 149, 184, 205, 217, 222, 237, 243 | **Low** | Multiple `await asyncio.sleep(0.05/0.1)` calls after `worker.stop()` to let NATS drain finish. These are tolerable but add cumulative delay (~0.6 s per test). | Tighten to `asyncio.sleep(0)` (scheduler yield) or use `worker._nc.drain()` synchronously if the mock/test double exposes it. |
| `packages/roxabi-nats/tests/test_image_testing_doubles.py` | 272, 293, 316, 328, 333, 348, 354 | **Low** | Same pattern as voice testing doubles — `asyncio.sleep(0.05/0.1)` post-stop. | Same recommendation as voice testing doubles. |
| `packages/roxabi-contracts/tests/test_voice_builders.py` | 32–215 | **Low** | Four builder tests (`build_stt_response_success`, `build_stt_response_error`, `build_tts_response_success`, `build_tts_response_error`) share an identical Arrange-Act-Assert shape but are written as separate methods. | Parametrize over `(builder_fn, ok, expected_fields)` to collapse 4 methods into 1, improving maintainability. |
| `packages/roxabi-blobs/tests/test_ingest.py` | 71–80 | **Low** | `test_ingest_source_stored_verbatim` loops over 4 source strings inline instead of using `@pytest.mark.parametrize`. | Extract the loop body into a parametrized test with `sources = ["telegram_voice", "discord_audio", "slack_audio", "cli_file"]`. |
| `packages/roxabi-nats/tests/test_voice_testing_doubles.py` | 276–309 | **Low** | `test_start_subscribe_failure_closes_connection_tts` and `_stt` are nearly identical (only `cls` differs). Same pattern exists in `test_image_testing_doubles.py`. | Parametrize over `(FakeTtsWorker, FakeSttWorker)` — the test body is class-agnostic. |
| `packages/roxabi-nats/tests/test_adapter_base.py` | 1029 | **Low** | `await asyncio.sleep(9999)` in a forever-task helper for shutdown cancellation test. While intentional, an `asyncio.Event` that never sets is a clearer signal of "intentionally blocked forever". | Replace `sleep(9999)` with `await asyncio.Event().wait()` to make the intent explicit and avoid the magic number. |
| `packages/roxabi-nats/tests/test_circuit_breaker.py` | 95–104 | **Low** | `test_custom_recovery_timeout` asserts on real `time.monotonic()` with a 0.1 s tolerance. Not flaky in practice, but uses unmocked time. | Mock `time.monotonic` to remove the tolerance window and make the assertion exact. |
| `packages/roxabi-nats/tests/test_driver_base.py` | 258 | **Low** | `await asyncio.sleep(0.05)` in the keepalive pump of `_dict_stream_gen_absolute_deadline`. The 50 ms sleep is part of the test choreography, but if the event loop is heavily loaded the absolute deadline may trip before all expected keepalives are processed. | Document the coupling or increase the `max_total_duration` margin so the test is resilient to slow CI. |

---

## Metrics

| Metric | roxabi-contracts | roxabi-nats | roxabi-blobs | Combined |
|--------|------------------|-------------|--------------|----------|
| Test files (excl. conftest/helpers) | 19 | 12 | 10 | 41 |
| Test lines | ~3,200 | ~4,800 | ~1,900 | ~9,900 |
| Stmts in package source | 677 | 1,026 | 297 | 2,000 |
| Missed stmts | 23 | 169 | 8 | 200 |
| **Coverage** | **96%** | **79%** | **95%** | **86%** |
| `@pytest.mark.parametrize` uses | 18 | 24 | 14 | 56 |
| `asyncio.sleep` / `time.sleep` in tests | 2 (conftest poll) | 16 | 1 (`sleep(0)`) | 19 |
| Real NATS server subprocess tests | 0 | 8 files | 0 | 8 |
| Mock/MonkeyPatch usage | Moderate | Heavy (NATS client) | Moderate (os/conn) | — |

### Coverage gaps (term-missing summary)

- **roxabi-contracts**: `voice/testing.py` lines 9–10 (60%). All other source files at 100%.
- **roxabi-nats**: `_serialize.py` lines 80, 107, 129, 155, 173, 179, 182, 185, 188, 191, 194, 215–232, 240, 243, 246–252, 257–263, 271, 273, 279–283, 296–298, 302, 306–308, 312, 324, 338–341, 345, 361 (54% — largest gap). `adapter_base.py` lines 65–75, 100, 146, 169–190, 206–207, 281, 286 (75%). `testing/voice.py` lines 78–82, 123, 133, 161–162, 191–195, 200, 234, 237–239, 242, 244, 266–267 (82%).
- **roxabi-blobs**: `http_store.py` lines 63, 70→exit, 83→85, 110, 112, 114, 124, 173, 181→184 (87% — HttpBlobStore is RED). All other source files at 100%.

---

## Recommendations (prioritized, max 5)

1. **Fix collection-breaking RED import in `test_protocol_parametrized.py`** (High). The top-level `HttpBlobStore` import prevents the entire parametrized protocol-equivalence test suite from running. Either skip the file or move the import behind a lazy fixture gate so the FsBlobStore variant can still provide coverage.

2. **Eliminate hardcoded `asyncio.sleep(0.2)` race guesses in readiness tests** (Medium). Replace with deterministic synchronization in `test_readiness.py` so KV watch tests do not silently pass/fail based on CI runner load.

3. **Parametrize unrolled builder success/error tests in `test_voice_builders.py`** (Low). Four near-identical methods covering the same matrix (`stt/tts` × `ok=True/False`) should be collapsed into one parametrized test. Same opportunity exists for `test_voice_testing_doubles.py` `_tts`/`_stt` pairs.

4. **Tighten post-stop sleep delays in testing-double tests** (Low). The 50–100 ms sleeps in voice/image testing doubles are empirically unnecessary on a local event loop; replacing them with `asyncio.sleep(0)` scheduler yields would cut ~5–10 s from the roxabi-nats test suite without added flakiness.

5. **Backfill coverage for `_serialize.py` missed branches** (Low). `_serialize.py` is the single largest uncovered surface in roxabi-nats (54%). The missing lines cluster around error-path type-hint resolution and union-type handling — exactly the paths most likely to regress during the stage-axis refactor. Adding targeted unit tests for `_EMPTY_RESOLVER` fallback and union-type dispatch would close the gap.
