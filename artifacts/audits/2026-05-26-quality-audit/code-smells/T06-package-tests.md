### Summary
- **roxabi-nats coverage gap is the outlier**: 79% vs 95-96% for the other two packages; `_serialize.py` (54%) and `testing/__init__.py` (35%) drive most of the miss.
- **Sleep-based waiting appears in 7 tests + conftest**: `asyncio.sleep` / `time.sleep` used for real-time race conditions and heartbeat loops instead of virtual clocks or synchronization primitives.
- **Heavy duplication across image/voice testing-double suites**: ~50 lines of identical guard logic (G1/G2/G3) repeated verbatim in both `test_image_testing_doubles.py` and `test_voice_testing_doubles.py`.

### Findings

| File | Line | Severity | Description | Recommendation |
|---|---|---|---|---|
| `packages/roxabi-nats/src/roxabi_nats/_serialize.py` | — | High | 54% coverage (173 stmts, 67 miss). Many error paths, Enum/bytes/datetime branches, and nested deserialization paths are never exercised. | Add unit tests for `_encode`/`_decode` branches: Enum, bytes b64 round-trip, datetime isoformat, callable skip, and nested dataclass resolution failures. |
| `packages/roxabi-nats/src/roxabi_nats/testing/__init__.py` | — | High | 35% coverage (13 stmts, 7 miss). The `__init__` re-export layer is untested. | Add a single test asserting the public API surface of `roxabi_nats.testing`. |
| `packages/roxabi-contracts/src/roxabi_contracts/llm/builders.py` | — | High | 50% coverage (16 stmts, 8 miss). No test file covers `build_llm_response` or `build_llm_chunk`. | Create `test_llm_builders.py` mirroring `test_voice_builders.py` (success path, error path, trace_id fallback, missing request_id). |
| `packages/roxabi-nats/tests/test_readiness.py` | 367, 460 | Medium | `asyncio.sleep(0.2)` used to create a race between `wait_for_hub` and `announce_hub_ready`. Timing-sensitive and may flake under CI load. | Replace with an `asyncio.Event` or barrier: start the probe, wait for it to reach the watch path (e.g. via a monkeypatched hook), then announce. |
| `packages/roxabi-nats/tests/test_adapter_base.py` | 1101, 1267 | Medium | `asyncio.sleep(0.01)` used to let `run()` reach heartbeat-task creation before `stop.set()`. Fragile on slow runners. | Inject a synchronization event into `run()` (or monkeypatch `asyncio.create_task`) so the test waits for the exact state transition. |
| `packages/roxabi-nats/tests/conftest.py` | 97, 143 | Medium | `time.sleep(0.05)` in nats-server startup polling loop. Acceptable for subprocess readiness, but lacks a fast-path when the server is already listening. | Reduce sleep to `0.01` and add a short `asyncio.sleep(0)` yield in the fixture consumer to amortize any jitter. |
| `packages/roxabi-nats/tests/test_driver_base.py` | 184, 220 | Medium | Real `time.monotonic()` used for `_worker_freshness` assertions in `TestIsAlive` and `TestAnyWorkerAlive`. Tests are deterministic today but will drift if TTL logic changes. | Patch `time.monotonic` (as done in `test_circuit_breaker.py`) to freeze the clock and make thresholds explicit. |
| `packages/roxabi-nats/tests/test_image_testing_doubles.py` | 38-84 | Medium | G1/G2/G3 guard tests are copy-pasted almost verbatim from `test_voice_testing_doubles.py` (lines 38-84). | Extract a shared `TestingDoublesGuardTests` mixin or parametrized fixture in a `tests/_testing_double_guards.py` module imported by both suites. |
| `packages/roxabi-nats/tests/test_nats_connect.py` | 47-71 | Low | Every test repeats identical `AsyncMock` + `patch("roxabi_nats.connect.nats.connect")` boilerplate (6 instances). | Extract a session-scoped or class-scoped fixture that yields a patched `nats_connect` context. |
| `packages/roxabi-blobs/tests/test_ingest.py` | 72-80 | Low | `test_ingest_source_stored_verbatim` loops over sources inline instead of using `@pytest.mark.parametrize`. | Convert the inline loop to a parametrized test for clearer failure isolation. |
| `packages/roxabi-contracts/tests/test_voice_builders.py` | 29-78 | Low | `build_stt_response` success/error and `build_tts_response` success/error are separate test functions with duplicated JSON-load + field-assertion blocks. | Parametrize over `(builder, ok, expected_fields)` to collapse 4 test bodies into 1 parametrized test. |
| `packages/roxabi-nats/tests/test_adapter_base.py` | 510-720 | Low | `run()` tests each build the same `mock_nc` with 4 identical AsyncMock attributes (lines 518-522, repeated 6x). | Create a `_make_mock_nc` helper or fixture, as already done in `test_driver_base.py`. |
| `packages/roxabi-contracts/tests/test_llm_models.py` | — | Low | No negative path for `build_llm_response`/`build_llm_chunk` (builders untested entirely). | See High recommendation above; also add a missing-request_id negative test. |
| `packages/roxabi-nats/tests/test_readiness.py` | 282-288 | Low | `test_idempotent_second_call` manually purges KV key to avoid cross-test bleed. Relies on global mutable JetStream state. | Use a per-test KV bucket name (via `tmp_path` + `-sd` on the server fixture) or reset the KV in a fixture teardown. |

### Metrics

| Package | Tests | Coverage | Missed stmts | Key gaps |
|---|---|---|---|---|
| roxabi-blobs | 91 | 95% | 8 | `fs_store.py` 67-68, 108->111, 162->167, 337->exit; `http_store.py` 6 miss |
| roxabi-contracts | 337 | 96% | 23 | `llm/builders.py` 50%; `image/testing.py` 60%; `voice/testing.py` 60% |
| roxabi-nats | 334 | 79% | 169 | `_serialize.py` 54%; `testing/__init__.py` 35%; `adapter_base.py` 75% |
| **Total** | **762** | **~87% weighted** | **200** | — |

**Sleep-based waiting count**: 9 occurrences across 5 files (`conftest.py` x2, `test_readiness.py` x2, `test_adapter_base.py` x2, `test_driver_base.py` x2, `test_image_testing_doubles.py` x3).

**Mock-overuse flags**: 0 tests mock the module-under-test directly (all mocks target `nats.connect`, `nats_connect`, or `nc` which are external/infra boundaries — acceptable).

**Naming conformance**: ~92% of tests use `test_<unit>_<condition>_<expected>`. Exceptions: parametrized `test_roundtrip` in contracts (acceptable because `ids` provide context), and abbreviated guard names like `test_g1_import_without_extra` (acceptable with docstrings).

**Parametrize opportunities identified**: 6 files with 2+ near-identical test bodies that could collapse into parametrized cases (see Findings table).

### Recommendations (prioritized)

1. **Close the roxabi-nats `_serialize.py` coverage hole** — add targeted unit tests for the untested 67 statements (Enum, bytes, datetime, nested dataclass, and error branches). This single file accounts for ~40% of the package's total coverage deficit.
2. **Create `test_llm_builders.py`** for `roxabi_contracts.llm.builders` — the only uncovered builder module; mirror the pattern already established in `test_voice_builders.py`.
3. **Replace real-time sleeps in readiness race tests** with virtual-time or event-based synchronization (`test_readiness.py` lines 367/460). These are the most likely to flake under CI resource contention.
4. **Deduplicate image/voice testing-double guard tests** by extracting a shared parametrized test module. ~100 lines of near-identical code across two files.
5. **Patch `time.monotonic` in `test_driver_base.py` `TestIsAlive` / `TestAnyWorkerAlive`** instead of using real clock comparisons. Aligns with the already-correct pattern in `test_circuit_breaker.py`.
