# T04 Quality Audit — nats / transport / llm / streaming tests

**Date:** 2026-05-27
**Partition:** `tests/nats/**/*.py`, `tests/transport/**/*.py`, `tests/llm/**/*.py`, `tests/streaming/**/*.py`
**Context:** Epic #1277 stage-axis refactor active; prior 2026-05-18 audit covered hexagonal conformance / duplication / dead-code. This audit focuses on test code quality only.

---

### Summary

- **Coverage is polarized:** streaming 98%, transport 87%, llm 75%, nats core modules 90-100%, but **4 source modules have 0% coverage** and `worker_pool_client` sits at 64%.
- **Flaky sleeps in NATS integration layer:** 7 `asyncio.sleep()` calls (0.05s-0.5s) in nats tests race real NATS delivery; queue-group test is most fragile at 0.5s.
- **Parametrize underutilized:** ~12 repeated test bodies across `render_event_codec`, `turn_publisher`, `serialize_outbound`, and the three thin-client files.

---

### Findings

| File | Line | Severity | Description | Recommendation |
|---|---|---|---|---|
| `src/lyra/nats/nats_tts_codec.py` | — | high | 0% coverage; no tests exist for TTS codec | Add unit tests mirroring `test_nats_image_client.py` / `test_nats_stt_client.py` codec success/error paths |
| `src/lyra/nats/tts_engine_selector.py` | — | high | 0% coverage; engine selection logic untested | Add unit tests for engine selection rules and fallback behavior |
| `src/lyra/nats/tts_text_normalization.py` | — | high | 0% coverage; text normalization untested | Add unit tests for normalization edge cases (punctuation, unicode, length) |
| `src/lyra/llm/cli_pool_codec.py` | — | high | 0% coverage; no tests exist for CLI pool codec | Add encode/decode round-trip and error-path tests |
| `tests/nats/test_nats_bus.py` | 521 | medium | `asyncio.sleep(0.5)` races NATS delivery in queue-group distribution test | Replace with `asyncio.Event` driven by subscription callback or bounded retry loop |
| `tests/nats/test_nats_bus_multibot.py` | 113 | medium | `asyncio.sleep(0.15)` waits for NATS delivery in multibot staging test | Replace with `asyncio.wait_for(subscriber.get(), timeout=...)` polling or event-driven drain |
| `tests/nats/test_hub_standalone.py` | 265, 355 | medium | Multiple `asyncio.sleep(0.05-0.1)` waits for Hub.run() side effects | Use `asyncio.Event` or `asyncio.Condition` signaled by stub instead of blind sleep |
| `src/lyra/transport/worker_pool_client.py` | 85-137 | medium | 36% uncovered; `stream_request` error paths, CB fallback, retry logic not exercised | Add tests for `stream_request` failure branches and CB half-open state |
| `tests/nats/test_nats_bus.py` | 426 | low | `asyncio.sleep(0.1)` in `test_staging_qsize` | Use `asyncio.wait_for` or event-driven assertion |
| `tests/nats/test_keepalive.py` | 125 | low | `await asyncio.sleep(5 * fast_interval)` uses real event loop timing despite monkeypatched interval | Acceptable given monkeypatch; consider `freezegun` or `asyncio` time mocking for determinism |
| `tests/nats/test_render_event_codec.py` | 305-421 | low | 4 repeated encode bodies + 4 repeated round-trip bodies + 4 repeated schema-floor bodies for Text triplet | Collapse into `@pytest.mark.parametrize` over event class + payload factory |
| `tests/nats/test_render_event_codec.py` | 137-199 | low | 5 ToolCall round-trip tests share identical assertion pattern | Parametrize over `(event_class, sample_kwargs)` |
| `tests/nats/test_render_event_codec.py` | 245-303 | low | 3 Reasoning round-trip tests share identical assertion pattern | Parametrize over `(event_class, sample_kwargs)` |
| `tests/nats/test_serialize_outbound.py` | 67-121 | low | 3 OutboundAttachment round-trip tests share identical assertion pattern | Parametrize over `(type, mime, filename, has_optional)` tuples |
| `tests/transport/test_turn_publisher.py` | 59-267 | low | 5 publish methods each have near-identical kind/subject assertions (10+ repeated bodies) | Parametrize over `(method_name, expected_kind, extra_field_checks)` |
| `tests/nats/test_nats_image_client.py` / `test_nats_stt_client.py` / `test_nats_tts_client.py` | — | low | Cross-file structural triplication: availability, success, codec error, pool error, start/stop | Extract shared parametrized test matrix or fixture factory for thin NATS clients |
| `src/lyra/llm/decorators.py` | 93-99 | low | 14% uncovered; decorator edge cases partially tested | Add test for `RetryDecorator` exponential backoff with non-zero base and half-open CB transition |
| `src/lyra/llm/drivers/cli.py` | 31-52 | low | 15% uncovered; CLI driver stream/error paths not exercised | Add test for `stream()` error propagation and `complete()` with `messages` kwarg actually used |
| `tests/nats/test_sanitize_single_entry.py` | 42-46 | info | Inventory test validates empty `EXPECTED_CALL_SITES` (sanitization removed) | Consider deleting or converting to ADR note once architecture is stable |

---

### Metrics

| Metric | Value |
|---|---|
| Test files analyzed | 33 |
| Total test cases | 468 (nats 244 + transport 109 + llm 75 + streaming 40) |
| Streaming coverage | 98% |
| Transport coverage | 87% |
| LLM coverage | 75% |
| NATS core modules coverage | 90-100% |
| Modules at 0% coverage | 4 (`nats_tts_codec`, `tts_engine_selector`, `tts_text_normalization`, `cli_pool_codec`) |
| Sleep-based waits | 7 across 5 files |
| Parametrize candidates | ~12 repeated test bodies |
| xdist internal error | 1 (transport suite; serial rerun required for coverage) |

---

### Recommendations (prioritized)

1. **Add tests for 0%-coverage modules.** `nats_tts_codec`, `tts_engine_selector`, `tts_text_normalization`, and `cli_pool_codec` are completely untested. Mirror the existing thin-client/codec test patterns from `test_nats_image_client.py` and `test_nats_stt_client.py`.
2. **Replace sleep-based waits with event-driven assertions.** The 7 `asyncio.sleep()` calls in nats integration tests (especially `test_nats_bus.py:521` at 0.5s) are the top flakiness vector. Use `asyncio.Event` or callback-driven assertions, or at minimum bound with retry loops.
3. **Parametrize repeated test bodies.** Collapse the Text/ToolCall/Reasoning encode+round-trip+schema-floor repetitions in `test_render_event_codec.py`, the OutboundAttachment variants in `test_serialize_outbound.py`, and the TurnPublisher method tests in `test_turn_publisher.py`. Reduces maintenance when new event types are added.
4. **Increase `worker_pool_client.py` coverage from 64% to >=90%.** Add tests for `stream_request` error paths, circuit-breaker half-open behavior, and retry exhaustion in `request_with_routing`. These are critical resilience paths.
5. **Cross-file test mutualization for thin NATS clients.** `test_nats_image_client.py`, `test_nats_stt_client.py`, and `test_nats_tts_client.py` are structurally identical (availability, success, codec error, pool error, start/stop delegation). Extract a shared parametrized test matrix to eliminate triplication.
