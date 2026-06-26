# T01 Core Tests — Code Smell Audit 2026-05-26

### Summary

- **Coverage at 34%** for `src/lyra/core` with critical stage-axis files (pool_processor, outbound dispatch, session lifecycle) below 20% — high regression risk during Epic #1277.
- **55 real `asyncio.sleep` calls** across 14 test files create flaky, wall-clock-dependent tests; the worst offender (`test_outbound_dispatcher_media.py`) has 12 sleeps in a single 356-line file.
- **Parametrize severely underused** — only 2 `@pytest.mark.parametrize` decorators across 122 test files and ~1655 test items; boundary-validation suites (`test_runtime_config_set_param.py`, `test_config_dataclasses.py`) repeat identical Arrange-Act-Assert blocks.

### Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `tests/core/test_outbound_dispatcher_media.py` | 51, 71, 89, 116, 136, 154, 184, 218, 245, 277, 314, 348 | High | 12 `asyncio.sleep(0.05)` calls used to wait for dispatcher worker loop. Fragile under load / CI. | Replace with `asyncio.Event` or queue-drain helpers (`_queue.join()` + `_scope_tasks` gather). |
| `tests/core/test_outbound_dispatcher_coverage.py` | 58, 87, 109, 135, 161, 200, 269, 295, 320, 405, 408 | High | 11 `asyncio.sleep` calls (0.05–0.1 s) in retry/callback/routing tests. | Use `asyncio.Event`/`asyncio.wait_for` or patch `asyncio.sleep` (pattern already used at L239/L361). |
| `tests/core/test_outbound_dispatcher_queue.py` | 36, 54, 75, 115, 147, 170, 191 | High | 7 `asyncio.sleep(0.05)` calls waiting for queue worker. | Use `_queue.join()` + `_scope_tasks` gather instead of arbitrary sleeps. |
| `tests/core/test_runtime_config_set_param.py` | 24–248 | Medium | 28 boundary tests (`valid_style_detailed`, `temperature_min_boundary_accepted`, …) with identical 3-line Arrange-Act-Assert structure, no parametrize. | Collapse into 2–3 `@pytest.mark.parametrize` tables (valid/invalid × param). |
| `tests/core/test_config_dataclasses.py` | various | Medium | Repeated `default_values`, `custom_values`, `frozen_immutability`, `equality` tests across dataclasses (3+ copies each). | Parametrize over `(dataclass_cls, expected_defaults)` tuples. |
| `tests/core/test_memory_manager.py` | 97–98, 116–117 | Medium | Patches `lyra.core.memory.memory.AsyncMemoryDB` and `apply_schema_compat` — mocks the module under test itself. | Refactor to inject DB dependency or test against an in-memory/fake implementation. |
| `tests/core/test_pool_tasks.py` | 74–315 | Low | Internal state injection via `ctx_mock._agents["test_agent"] = agent` in 12 tests. | Use fixture factory to reduce noise; not a smell per se but adds boilerplate. |
| `tests/core/test_debouncer_pool.py` | 34–180 | Low | Same `_make_ctx_mock({"test_agent": agent})` repeated 5×; tests are structurally identical except debounce_ms value. | Parametrize over `debounce_ms` and expected call count. |
| `tests/core/test_hub_circuit_streaming.py` | 70, 133, 196, 278 | Medium | 4 `asyncio.sleep(0.1–0.2)` in circuit-breaker streaming tests. | Replace with deterministic `Event` or mock time (monkeypatch `asyncio.sleep`). |
| `tests/core/test_audio_pipeline_tts.py` | 303, 493 | Medium | 2 `asyncio.sleep(0.1)` calls in TTS pipeline tests. | Use `Event`-based synchronization or mock `asyncio.sleep`. |
| `tests/core/test_turn_store.py` | 121, 320, 429, 454 | Low | 4 `asyncio.sleep(0.01)` to force distinct timestamps. | Use `freezegun` or inject a mock clock; `sleep(0.01)` is a race under fast CI runners. |
| `tests/core/test_command_router_special.py` | 162–167 | Medium | `asyncio.wait_for(hub.run(), timeout=0.3)` + swallowed `TimeoutError` — test relies on wall-clock timeout and silently passes on timeout. | Use explicit event-based completion or a dedicated test double for Hub run-loop. |
| `tests/core/test_outbound_dispatcher_concurrent.py` | 50, 96, 180, 189 | Low | `asyncio.sleep` inside `side_effect` send functions (intentional slowness) plus one yield sleep. | Acceptable for concurrency differential tests; document why wall-clock is required. |
| `tests/core/test_config_dataclasses.py` | various | Low | 10× `equality(self)` and 7× `frozen(self)` duplicate method names across classes — safe because class namespace disambiguates, but noisy in pytest output and IDEs. | Consolidate or rename to `test_<class>_equality`. |

### Metrics

| Metric | Value |
|--------|-------|
| Test files (`tests/core/**/*.py`) | 122 |
| Test items (pytest collection) | ~1655 |
| Lines of test code | ~31,100 |
| `src/lyra/core` coverage | **34%** |
| Files with coverage < 20% | 16 |
| Files with coverage < 50% | 22 |
| `asyncio.sleep(≥0.01)` calls in tests | **55** |
| Files containing real sleeps | 14 |
| `@pytest.mark.parametrize` uses | **2** |
| `MagicMock` / `AsyncMock` references (excl. conftest) | 684 |
| `patch(...)` references (all) | 1,795 |
| Duplicate test method names (top) | `equality` (10×), `frozen` (7×) |
| Tests with condition-expected naming | ~368 / 1655 (~22%) |

#### Lowest-coverage `src/lyra/core` files ( Epic #1277 hotspots )

| File | Coverage | #1277 Relevance |
|------|----------|-----------------|
| `persona.py` | 5% | Agent config |
| `hub/outbound/_dispatch.py` | 6% | Outbound dispatch |
| `pool/pool_processor_exec.py` | 9% | Pool execution |
| `commands/builtin_commands.py` | 9% | Command routing |
| `pool/pool_processor.py` | 11% | Pool processing |
| `hub/middleware/middleware_pool.py` | 16% | Middleware stage |
| `hub/pipeline/pool_manager.py` | 16% | Pipeline manager |
| `hub/outbound/outbound_streaming.py` | 14% | Streaming outbound |
| `hub/outbound/outbound_tts.py` | 16% | TTS dispatch |
| `session_lifecycle.py` | 25% | Session stage |
| `pool/pool.py` | 79% | Pool core |
| `hub/outbound/outbound_dispatcher.py` | 91% | Dispatcher core |

### Recommendations (prioritized)

1. **Eliminate wall-clock sleeps in dispatcher tests** — The 30 sleeps in `test_outbound_dispatcher_{media,coverage,queue}.py` are the densest flaky cluster. Replace with `asyncio.Event`, `_queue.join()`, or gather `_scope_tasks`. Estimated effort: medium; impact: high CI stability.

2. **Parametrize boundary-validation suites** — `test_runtime_config_set_param.py` (28 tests) and `test_config_dataclasses.py` (~12 repeated triplets) should use `@pytest.mark.parametrize`. This cuts ~150 lines of boilerplate and makes adding new boundaries trivial. Estimated effort: low.

3. **Add coverage for stage-axis cold spots** — `pool_processor.py` (11%), `pool_processor_exec.py` (9%), `_dispatch.py` (6%), `outbound_streaming.py` (14%), and `session_lifecycle.py` (25%) are actively changing in Epic #1277 and have almost no test harness. Target these before further refactor. Estimated effort: high; impact: prevents regressions.

4. **Stop mocking the module under test** — `test_memory_manager.py` patches `lyra.core.memory.memory.AsyncMemoryDB` (the direct collaborator of the SUT). Switch to an in-memory SQLite double or a `MemoryManager` factory that accepts a DB interface. Estimated effort: medium.

5. **Standardize test naming** — ~78% of test names do not follow `test_<unit>_<condition>_<expected>`. Rename the most ambiguous clusters (`equality`, `frozen`, `default_values`) and enforce the convention in PR review for new tests. Estimated effort: low; do incrementally.
