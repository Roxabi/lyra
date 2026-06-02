# T02 Code Smell Audit — adapters & bootstrap tests

**Date:** 2026-05-27
**Scope:** `tests/adapters/**/*.py`, `tests/bootstrap/**/*.py`
**Context:** Epic #1277 stage-axis refactor active; prior audit 2026-05-18 covered hexagonal conformance — this audit focuses on test code quality only.

---

## Summary

- **Bootstrap coverage lags adapters by 27 pp** (55% vs 82%); multiple standalone/factory files are at 0-20% with zero test representation.
- **Flaky async synchronization** persists: real `asyncio.sleep` in telegram typing tests, `for _ in range(5): await asyncio.sleep(0)` race workarounds in bootstrap audit tests, and manual module-level `asyncio.sleep` mutation in discord outbound tests.
- **Mock overuse is systemic** in clipool worker resume (4x patching `_kill`), render attachment/audio tests (26x patching `get_channel`), and 13 inline mock imports in `test_discord_threads.py`; only **1 `@pytest.mark.parametrize`** across 627 test functions.

---

## Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `tests/adapters/test_telegram_typing.py` | 50 | High | Real `await asyncio.sleep(interval * 5)` used to wait for background refresh; timing-dependent and flaky on slow runners. | Drive timing via `asyncio.Event` or mock the loop timer directly; never sleep in tests. |
| `tests/adapters/test_telegram_typing.py` | 72 | High | Real `await asyncio.sleep(0.12)` to "confirm no further calls after exit". | Use `asyncio.Event` or assert on mock call count immediately after context exit without wall-clock wait. |
| `tests/adapters/test_telegram_typing.py` | 109 | High | Real `await asyncio.sleep(0.1)` to confirm cancellation in typing loop. | Same as above — remove wall-clock dependency. |
| `tests/bootstrap/test_audit_bootstrap_wiring.py` | 111-112 | High | `for _ in range(5): await asyncio.sleep(0)` to yield control and let spawn events propagate — race-condition workaround. | Replace with deterministic synchronization (e.g., `asyncio.Event` signaled by patched `sink.emit`). |
| `tests/bootstrap/test_audit_bootstrap_wiring.py` | 140-141 | High | Identical `range(5)+sleep(0)` workaround duplicated for `skip_permissions=False` test. | Extract shared helper or use event-based sync; also parametrize the two tests. |
| `tests/adapters/test_discord_outbound.py` | 753-760 | High | Directly mutates `_outbound_mod.asyncio.sleep = _AsyncMock()` and restores in `finally` — bypasses `patch()` context, leaks on exception, and is brittle against refactor. | Use `patch("lyra.adapters.discord.discord_outbound.asyncio.sleep", ...)` context manager. |
| `tests/adapters/test_discord_outbound.py` | 781-786 | High | Same manual module-level sleep mutation pattern repeated. | Consolidate into a single fixture or context manager. |
| `tests/adapters/test_discord_threads.py` | 273-558 | Medium | 13 inline `from unittest.mock import AsyncMock, MagicMock, patch` imports scattered inside test methods and classes. | Move all mock imports to module top; inline imports are a leftover RED-phase habit. |
| `tests/adapters/test_discord_outbound.py` | 349, 368, 733, 768 | Medium | Inline `from unittest.mock import MagicMock / AsyncMock` imports inside test functions. | Move to module top. |
| `tests/adapters/test_bootstrap_wiring.py` | 44, 88 | Medium | Inline `from unittest.mock import AsyncMock` inside two test functions. | Move to module top. |
| `tests/adapters/test_telegram_auth.py` | 189 | Medium | Inline `from unittest.mock import patch` inside test method. | Move to module top. |
| `tests/adapters/test_clipool_worker.py` | 119-120 | Medium | `patch.object(worker, "_handle_control")` and `patch.object(worker, "_handle_cmd")` — mocks internal methods of the unit under test. | Test via public API or state inspection; mocking internal seams weakens regression value. |
| `tests/adapters/clipool/test_clipool_worker_resume.py` | 108, 128, 144, 158 | Medium | 4 repeated `patch.object(pool, "_kill", ...)` blocks across tests that differ only in preconditions (`_entries` state and session_id). | Parametrize into a single test or use a fixture matrix. |
| `tests/adapters/test_render_attachment_discord.py` | 51-304 | Medium | 14 `patch.object(adapter, "get_channel")` calls — every test mocks the same dependency with the same return value. | Extract a fixture that yields `adapter` with `get_channel` pre-patched. |
| `tests/adapters/test_render_audio_stream.py` | 183-261 | Medium | 7 `patch.object(adapter, "get_channel")` calls with identical return value. | Same fixture extraction recommendation. |
| `tests/adapters/test_render_audio.py` | 142-231 | Medium | 5 `patch.object(adapter, "get_channel")` calls with identical return value. | Same fixture extraction recommendation. |
| `tests/adapters/test_discord_auth.py` | 131 | Medium | `patch.object(adapter, "normalize")` — mocks a method of the adapter under test. | Test via inbound message flow or spy on `inbound_bus.put` instead. |
| `tests/adapters/test_telegram_auth.py` | 180 | Medium | `patch.object(adapter, "normalize")` — mocks a method of the adapter under test. | Same as above. |
| `tests/bootstrap/test_hub_builder.py` | 110 | Medium | `patch.object(hub, "register_agent")` — mocks method of object under test. | Assert on observable hub state instead of patching internal method. |
| `tests/adapters/test_shared.py` | 127-163 | Medium | `TestSendWithRetry` has 4 test functions with identical structure; only `side_effect` and `max_attempts` differ. | Use `@pytest.mark.parametrize` for attempts / side_effect / expected_call_count. |
| `tests/bootstrap/test_audit_bootstrap_wiring.py` | 88-144 | Medium | `test_audit_sink_skip_permissions_true` and `test_audit_sink_skip_permissions_false` are nearly identical except the boolean flag and assertion. | Parametrize by `skip_permissions` value and expected `events[0].skip_permissions`. |
| `src/lyra/adapters/telegram/telegram_formatter.py` | — | Medium | **0% coverage** (70 statements, 0 covered). Dead or untested code. | Add unit tests or delete if truly dead (verify with ADR-1277 scope). |
| `src/lyra/bootstrap/standalone/clipool_standalone.py` | — | Medium | **0% coverage** (27 statements). No tests exist. | Add standalone bootstrap tests for clipool entry point. |
| `src/lyra/bootstrap/standalone/turn_writer_standalone.py` | — | Medium | **0% coverage** (45 statements). No tests exist. | Add tests or document intentional omission (ADR-075 sole writer). |
| `src/lyra/bootstrap/factory/agent_store_factory.py` | — | Medium | **0% coverage** (16 statements). No tests exist. | Add factory wiring tests. |
| `src/lyra/bootstrap/factory/bot_agent_map.py` | — | Low | **12% coverage** — only a narrow success path tested. | Expand to cover missing bot/agent mapping edge cases. |
| `src/lyra/bootstrap/factory/utils.py` | — | Low | **11% coverage** — most helper branches uncovered. | Add unit tests for `factory/utils.py` edge cases. |
| `src/lyra/adapters/shared/_shared_audio.py` | — | Low | **24% coverage** — audio pipeline helpers largely uncovered. | Add tests for `_shared_audio.py` once audio refactor (#1277 slice) lands. |
| `src/lyra/adapters/shared/_tool_recap.py` | — | Low | **22% coverage** — tool recap formatter helpers uncovered. | Add tests if tool_recap remains in scope post-#1277. |

---

## Metrics

| Metric | Value |
|--------|-------|
| Total test functions | 627 |
| `@pytest.mark.parametrize` usage | 1 |
| Adapters source coverage | **82%** (2,805 stmts, 424 missing) |
| Bootstrap source coverage | **55%** (1,807 stmts, 758 missing) |
| Combined source tracked | 4,612 stmts |
| Real `asyncio.sleep` calls in tests | 4 |
| `patch.object(adapter, "get_channel")` calls | 26 |
| `patch.object(pool/adapter, "_kill"/"_handle_*"/"normalize")` calls | 9 |
| Inline mock import lines (not at module top) | ~25 (concentrated in 5 files) |
| 0% coverage files in scope | 4 (`telegram_formatter`, `clipool_standalone`, `turn_writer_standalone`, `agent_store_factory`) |

---

## Recommendations (prioritized)

1. **Eliminate wall-clock sleeps and race workarounds**
   Replace the 4 real `asyncio.sleep` calls in `test_telegram_typing.py` and the `range(5)+sleep(0)` loops in `test_audit_bootstrap_wiring.py` with deterministic async primitives (`asyncio.Event`, `asyncio.Condition`, or monkey-patched loop time). This is the highest flakiness risk.

2. **Introduce parametrization for repeated test matrices**
   Convert `TestSendWithRetry` (4 tests → 1 parametrize), `clipool/test_clipool_worker_resume.py` (4 tests → 1 parametrize with 3 cases), and the two `audit_sink_skip_permissions_*` tests. Target: move from 1 parametrize to ≥8 across T02.

3. **Backfill 0% coverage files**
   Add tests for `telegram_formatter.py`, `clipool_standalone.py`, `turn_writer_standalone.py`, and `agent_store_factory.py`. If any are intentionally untested (e.g., `turn_writer_standalone` is integration-only), document the exemption in `tools/file_exemptions.txt` per project quality gates.

4. **Centralize mock imports and extract fixtures**
   Move all inline `from unittest.mock import ...` statements to module top in `test_discord_threads.py`, `test_discord_outbound.py`, `test_bootstrap_wiring.py`, and `test_telegram_auth.py`. Create a shared `adapter_with_channel` fixture for the 26 `patch.object(adapter, "get_channel")` calls in render/audio/attachment tests.

5. **Reduce mocking of the module under test**
   Replace `patch.object(adapter, "normalize")` and `patch.object(worker, "_handle_control")` with state-based or boundary-based assertions (e.g., spy on `inbound_bus.put`, inspect `_entries` after public method calls). Where internals must be stubbed, use a factory fixture rather than inline patches.
