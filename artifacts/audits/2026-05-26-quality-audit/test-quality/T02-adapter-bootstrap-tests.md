# T02 Test Quality Audit — adapters + bootstrap

## Summary

- **Coverage gap is severe in bootstrap factory/infra**: 8 bootstrap modules sit at <35% coverage (agent_store_factory 0%, bot_agent_map 12%, unified 24%, wiring_helpers 32%, health 31%, lockfile 26%, notify 20%). These are precisely the wiring/orchestration modules that the prior hexagonal audit flagged as high-risk.
- **Parametrization is effectively unused**: 1 `@pytest.mark.parametrize` across 627 tests. Identical test skeletons repeat dozens of times (retry logic, sentinel checks, config-DB state transitions, permission true/false pairs).
- **Flaky timing patterns persist in 3 files**: `tests/adapters/test_telegram_typing.py` uses real `asyncio.sleep(0.05–0.12)` for typing-loop refresh assertions; `tests/bootstrap/test_audit_bootstrap_wiring.py` pumps the event loop with 5× `asyncio.sleep(0)`; `tests/adapters/test_discord_outbound.py` does manual module-level `asyncio.sleep` monkeypatch with try/finally restoration.

## Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `src/lyra/bootstrap/factory/agent_store_factory.py` | module | High | 0% coverage — 48 statements, never exercised by any bootstrap test. | Add unit tests for agent store factory resolution. |
| `src/lyra/bootstrap/factory/bot_agent_map.py` | module | High | 12% coverage — 34 statements, only 4 hit. Bot-to-agent mapping is core wiring. | Add tests for `build_bot_agent_map` edge cases (missing bots, duplicate IDs). |
| `src/lyra/bootstrap/factory/unified.py` | module | High | 24% coverage — 54 statements. Unified bootstrap entry point largely untested. | Add tests for `build_unified` error branches and overlay wiring. |
| `src/lyra/bootstrap/factory/wiring_helpers.py` | module | High | 32% coverage — 146 statements. Complex wiring logic (NATS, dispatcher, health) has no test surface. | Add targeted tests for `_wire_dispatcher`, health wiring, NATS subject construction. |
| `src/lyra/bootstrap/infra/health.py` | module | High | 31% coverage — 69 statements. Health probe endpoints untested. | Add tests for readiness/liveness endpoint handlers. |
| `src/lyra/bootstrap/standalone/hub_standalone.py` | module | High | 30% coverage — 131 statements. Standalone hub bootstrap mostly untested. | Expand `test_hub_standalone_readiness.py` to cover more than AST ordering. |
| `src/lyra/adapters/telegram/telegram_formatter.py` | module | Medium | 37% coverage — 70 statements. Telegram formatting helpers untested. | Add tests for `render_text`, `render_buttons`, `render_attachment` formatting. |
| `src/lyra/adapters/discord/discord_formatter.py` | module | Medium | 41% coverage — 65 statements. Discord formatting helpers untested. | Add tests for chunking, button rendering, embed formatting. |
| `tests/adapters/test_telegram_typing.py` | 50 | Medium | `await asyncio.sleep(interval * 5)` relies on real wall-clock timing for typing-loop refresh assertion. | Replace with a mockable clock or inject `asyncio.sleep` dependency; or use `AsyncMock` call-count assertions immediately after context exit. |
| `tests/adapters/test_telegram_typing.py` | 72 | Medium | `await asyncio.sleep(0.12)` waits for background task cancellation confirmation. | Use `asyncio.Event` or mock task done-state instead of real sleep. |
| `tests/adapters/test_telegram_typing.py` | 109 | Medium | `await asyncio.sleep(0.1)` same pattern — post-exit idle confirmation. | Same as above — use mock-based idle detection. |
| `tests/bootstrap/test_audit_bootstrap_wiring.py` | 111 | Medium | 5× `await asyncio.sleep(0)` to pump event loop for audit-sink emission. Flaky under CI load. | Replace with `asyncio.gather` on the spawn task plus explicit event synchronization. |
| `tests/adapters/test_discord_outbound.py` | 753 | Medium | Manual module-level `asyncio.sleep` monkeypatch (`original_sleep = …; _outbound_mod.asyncio.sleep = _AsyncMock(); … finally: restore`). Fragile if exception escapes before restore. | Use `monkeypatch` fixture or `patch.object` context manager for automatic cleanup. |
| `tests/adapters/test_discord_outbound.py` | 781 | Medium | Same manual monkeypatch pattern repeated in second typing-worker test. | Extract a fixture or context-manager helper. |
| `tests/adapters/test_shared.py` | 132 | Low | `with patch("asyncio.sleep", new_callable=AsyncMock):` repeated in 4 `TestSendWithRetry` tests. | Parametrize retry scenarios (success, 1 failure, max attempts, custom max). |
| `tests/bootstrap/test_bootstrap_stores.py` | 59 | Low | `TestHasSentinel` has 4 tests with identical Arrange/Act/Assert shape (tmp_path DB + bool). | Parametrize over `(setup_fn, expected)` tuples. |
| `tests/bootstrap/test_bootstrap_stores.py` | 107 | Low | `TestAtomicTableCopy` has 4 tests with identical shape (src/dst/tables + assert rows). | Parametrize over `(tables, expected_rows, dst_should_exist)` tuples. |
| `tests/bootstrap/test_bootstrap_stores.py` | 199 | Low | `_ensure_config_db` and `_ensure_discord_db` each have fresh/partial/complete/noop tests — 8 near-identical tests. | Parametrize over `(fn, db_name, precondition, expected_sentinel, expected_touch)` tuples. |
| `tests/bootstrap/test_audit_bootstrap_wiring.py` | 88 | Low | `skip_permissions_true` and `skip_permissions_false` are identical except one boolean value. | Single parametrized test with `@pytest.mark.parametrize("skip_permissions, expected", [(True, True), (False, False)])`. |
| `tests/bootstrap/test_hub_standalone_readiness.py` | 54 | Low | AST structural assertion (`_call_order` via `ast.parse`) is clever but fragile — breaks if helper methods or wrappers are introduced. | Add a behavioral test (call-sequence spy or mock assertion) alongside the structural guard. |
| `tests/adapters/test_discord_voice_session.py` | 195 | Low | `patch("lyra.adapters.discord.voice.discord_voice._check_voice_deps")` repeated 3× in one file. | Extract an autouse fixture or parametrized test class. |
| `tests/adapters/test_render_attachment_discord.py` | 36 | Low | `make_dc_attach_adapter() + mock_channel() + ref_msg.reply` boilerplate repeated in every test (8×). | Extract a fixture `dc_attachment_setup` returning `(adapter, channel, ref_msg)`. |
| `tests/adapters/test_render_attachment_telegram.py` | — | Low | Same `make_tg_attach_adapter() + mock_bot` boilerplate repeated 8×. | Extract a fixture `tg_attachment_setup`. |
| `tests/adapters/test_discord_normalize.py` | 17 | Low | `DiscordAdapter(..., intents=discord.Intents.none())` + `SimpleNamespace` message construction repeated in 8 tests. | Extract a `make_dc_adapter()` fixture or parametrized message builder. |
| `tests/adapters/test_telegram_normalize_fields.py` | 22 | Low | `TelegramAdapter(..., token="test-token-secret", inbound_bus=MagicMock())` + `SimpleNamespace` aiogram_msg repeated in 12 tests. | Extract a fixture `make_tg_adapter()` and parametrized message builder. |
| `tests/adapters/test_discord_outbound.py` | 42 | Low | `test_send_reply_on_mention` and `test_send_reply_on_no_mention` are identical bodies except `is_mention` flag, yet both assert `reply()` was called (naming mismatch — `no_mention` still calls `reply`). | Rename `test_send_reply_on_no_mention` to `test_send_replies_to_trigger_message_regardless_of_mention` or clarify behavior in docstring. |
| `tests/adapters/test_telegram_outbound_send.py` | 31 | Low | 605-line file; many tests reconstruct full `InboundMessage` inline rather than using conftest helpers. | Use `_make_telegram_message()` and `_make_telegram_adapter()` consistently. |
| `tests/adapters/test_nats_outbound_listener.py` | 1 | Low | 1145-line file — largest in partition. No sub-foldering or slice-based split. | Consider splitting by envelope type (`send`, `chunk`, `attachment`, `drain`). |
| `tests/bootstrap/test_adapter_standalone.py` | 34 | Low | Telegram and Discord bootstrap tests are near-identical (62 lines each) with only platform-specific strings changed. | Parametrize platform + config shape; reduce to one parametrized test. |

## Metrics

| Metric | Value |
|--------|-------|
| Total test functions | 627 |
| Total test classes | 91 |
| Lines of test code | ~18,125 |
| `@pytest.mark.parametrize` usages | 1 |
| `caplog` usages | 79 |
| Coverage (adapters + bootstrap combined) | 72% (4,612 stmts, 1,182 miss) |
| Adapter modules <80% coverage | 9 / 35 |
| Bootstrap modules <50% coverage | 11 / 28 |
| Flaky `sleep`-based tests | 7 tests across 3 files |
| Duplicate test names across files | 31 pairs |
| Test names with <3 underscore segments | 5 / 627 (<1%) |
| Test names with 4–6 segments (good) | 353 / 627 (56%) |
| Test names with 7+ segments (verbose) | 178 / 627 (28%) |

### Coverage by partition

| Partition | Stmts | Miss | Cover |
|-----------|-------|------|-------|
| `src/lyra/adapters/` | 2,586 | 524 | 80% |
| `src/lyra/bootstrap/` | 2,026 | 658 | 68% |

### Lowest-coverage bootstrap modules (under 50%)

| Module | Cover | Missing lines |
|--------|-------|-------------|
| `agent_store_factory.py` | 0% | 3–48 |
| `bot_agent_map.py` | 12% | 31–97 |
| `unified.py` | 24% | 41–93 |
| `notify.py` | 20% | 13–36 |
| `lockfile.py` | 26% | 16, 24–28, 38–75 |
| `health.py` | 31% | 30–37, 41, 55–63, 84, 88–145 |
| `wiring_helpers.py` | 32% | 96–97, 108–114, … 407 |
| `hub_standalone.py` | 30% | 63, 76–79, 86–87, 101, 109–302 |
| `hub_standalone_helpers.py` | 20% | 36–57, 66–74, 85–102, 116–127 |
| `agent_factory.py` | 20% | 40, 60–91, 102–107, 122–125, 140–191, 222–251 |
| `bootstrap_wiring.py` | 43% | 126–220, 240–254, 257–273 |
| `nats_wiring.py` | 44% | 51–76, 96–139 |

## Recommendations (prioritized)

1. **Close the bootstrap factory/infra coverage hole** — 11 modules under 50% coverage represent the highest-risk blind spot. Start with `agent_store_factory.py` (0%) and `wiring_helpers.py` (32%) because they gate agent resolution and NATS wiring correctness.

2. **Introduce parametrization for repeated test skeletons** — Targets: `TestSendWithRetry` (4 tests), `TestHasSentinel` (4 tests), `_ensure_config_db` / `_ensure_discord_db` (8 tests), Telegram vs Discord standalone bootstrap (2 tests), `skip_permissions` true/false pair. Expected reduction: ~15 tests → 4 parametrized tests.

3. **Eliminate real `asyncio.sleep` from typing and event-loop tests** — Replace `test_telegram_typing.py` sleeps with mock-clock injection or `AsyncMock` call-count assertions. Replace `test_audit_bootstrap_wiring.py` 5× `sleep(0)` with explicit `asyncio.Event` synchronization or `asyncio.gather` on the background task.

4. **Extract adapter-setup fixtures to reduce boilerplate** — `test_render_attachment_discord.py`, `test_render_attachment_telegram.py`, `test_discord_normalize.py`, and `test_telegram_normalize_fields.py` each repeat 8–12 near-identical setup blocks. Extract platform-specific fixtures into `conftest.py` or per-file fixtures.

5. **Split `test_nats_outbound_listener.py` (1,145 lines)** — Split by envelope type (`send`, `chunk/ streaming`, `attachment`, `drain / lifecycle`). This brings each file under the 300-line quality-gate limit and makes coverage gaps per envelope type visible.
