# T05 Test Quality Audit — misc tests (cli, commands, inbound, outbound, blobstore, agents, typing)

Date: 2026-05-27
Partition: T05
Scope: `tests/cli/**/*.py`, `tests/commands/**/*.py`, `tests/inbound/**/*.py`, `tests/outbound/**/*.py`, `tests/blobstore/**/*.py`, `tests/agents/**/*.py`, `tests/typing/**/*.py`

---

### Summary

- **Typing and Agents are excellent** (96–98% coverage, clean naming, good parametrization). **CLI commands and outbound formatter are blind spots** (0% coverage). **Inbound wire parsers** (0–46%) and **blobstore CLI** (0%) also lack tests.
- **Stale RED-phase framing** in 6+ test files: docstrings claim tests "MUST FAIL" or "do not yet exist" but all pass because implementations landed; comments were never updated.
- **4 `@pytest.mark.parametrize` across 252 test methods** — under-parametrized for the volume of near-identical test bodies (emitter smoke tests, router rules, voice-smoke failure paths).

---

### Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `src/lyra/commands/add_vault/handlers.py` | — | High | 0% coverage — module completely untested | Add unit tests for command handlers or merge into CLI tests if handlers are thin |
| `src/lyra/commands/identity/handlers.py` | — | High | 0% coverage — module completely untested | Add unit tests for command handlers |
| `src/lyra/commands/search/handlers.py` | — | High | 0% coverage — module completely untested | Add unit tests for command handlers |
| `src/lyra/outbound/formatter.py` | — | High | 0% coverage — no test file exists for formatter/throttle | Create `tests/outbound/test_formatter.py` covering Discord/Telegram formatters |
| `src/lyra/cli.py` | — | Medium | 38% coverage; large untested swaths in app factory and sub-command wiring | Add targeted tests for CLI boot path or move complex logic into testable modules |
| `src/lyra/cli_agent.py` | — | Medium | 34% coverage; agent CLI entry points under-tested | Extend `tests/cli/test_agent_cli_commands.py` beyond help/empty-DB smoke tests |
| `src/lyra/cli_agent_create.py` | — | Medium | 16% coverage; create wizard heavily under-tested | Add test cases for field validation and wizard branching |
| `src/lyra/inbound/wire_parser.py` | — | Medium | 0% coverage; base parser untested | Add tests for `wire_parser.py` base logic (separate from adapter-specific tests) |
| `src/lyra/inbound/wire_parser_discord.py` | — | Medium | 46% coverage; only half exercised | Add tests for edge-case platform meta extraction |
| `src/lyra/inbound/wire_parser_telegram.py` | — | Medium | 46% coverage; only half exercised | Add tests for edge-case platform meta extraction |
| `src/lyra/blobstore/cli.py` | — | Medium | 0% coverage; blobstore CLI untested | Add CLI smoke tests for blobstore subcommands |
| `src/lyra/inbound/session_builder.py` | — | Low | 80% coverage; 19 missing lines include error branches | Add error-path tests for store failure scenarios |
| `tests/typing/test_integration_nats.py` | 50, 55 | Medium | `asyncio.sleep(0.1)` used as synchronization primitive for NATS message delivery | Replace with `asyncio.Event` or `asyncio.wait_for` on a synchronization primitive; will flake under CI load |
| `tests/cli/test_cli_setup.py` | 66, 144 | Medium | Patches `_register_telegram_bot` — the operation under test is mocked away | Split into (a) orchestration test (current) and (b) integration test that exercises real registration logic with a fake Telegram server |
| `tests/cli/test_voice_smoke.py` | 117, 559 | Low | Heavy patching of `lyra.cli_voice_smoke.nats_connect` in the module under test | Acceptable for CLI smoke tests, but consider extracting a pure `VoiceSmokeRunner` class to reduce patching surface |
| `tests/inbound/test_pipeline.py` | — | Low | Mocks all 3 downstream stages (router, session_builder, dispatcher) | Acceptable for orchestration contract, but add at least one integration test that uses real stage instances through the pipeline |
| `tests/agents/test_simple_agent.py` | 349, 370, 390, 503, 531 | Low | Cryptic test names encode ticket IDs: `test_t8_reset...`, `test_t9a...`, `test_t12...`, `test_t13...`, `test_t8n...` | Rename to behavior-driven names: `test_reset_routes_through_cli_pool_not_provider`, `test_configure_pool_idempotent`, etc. |
| `tests/cli/test_bot_secret.py` | 1–5 | Low | Docstring claims tests "MUST FAIL until T2 implements" — all 21 tests pass | Remove stale RED-phase framing; update docstring to reflect current state |
| `tests/cli/test_agent_cli_commands.py` | 1–6 | Low | Docstring says tests "expected to fail (RED) until the implementation lands" — all 10 pass | Remove stale RED-phase framing |
| `tests/agents/test_simple_agent.py` | 342 | Low | Comment says "Tests T8–T13 will be RED until the backend fix lands" — all pass | Remove stale RED-phase comment |
| `tests/blobstore/test_audit.py` | 1, 13, 228, 249 | Low | RED-phase framing claims `audit_sink` "does not exist yet" and "T13 adds nats= kwarg; until then this call raises TypeError (RED)" — module exists and tests pass | Remove stale RED-phase comments |
| `tests/blobstore/test_http_store.py` | 1, 11 | Low | RED-phase framing claims `HttpBlobStore` "does not exist yet — RED" — import succeeds and tests pass | Remove stale RED-phase comments |
| `tests/outbound/test_emitter_discord_smoke.py` | — | Low | 10 `test_send_streaming_*` methods with near-identical Arrange/Act/Assert pattern | Parametrize over `(reply_context, expected_method, expected_call_count)` tuples |
| `tests/outbound/test_emitter_telegram_smoke.py` | — | Low | 9 `test_send_streaming_*` methods with near-identical Arrange/Act/Assert pattern | Parametrize over `(condition, assertion_fn)` tuples |
| `tests/inbound/test_router.py` | — | Low | 8 Discord routing tests are separate methods with only `platform_meta` and `expected` varying | Parametrize over `(platform_meta, expected_decision)` tuples; reduces ~70 lines |
| `tests/cli/test_voice_smoke.py` | — | Low | ~20 failure-path tests share identical `AsyncMock()` + `runner.invoke` + `assert result.exit_code == 1` pattern | Parametrize over `(side_effect, expected_output_substring)` tuples in a single test method |

---

### Metrics

| Metric | Value |
|--------|-------|
| Test files | 30 |
| Test lines (approx.) | 7,509 |
| Test methods/functions | ~252 |
| Test classes | ~76 |
| `@pytest.mark.parametrize` occurrences | 4 |
| `@pytest.mark.xfail` occurrences | 1 |
| `@pytest.mark.skip` / `@pytest.mark.skipif` occurrences | 1 |
| `asyncio.sleep` / `time.sleep` in tests | 2 |

**Coverage by partition**

| Partition | Module(s) | Coverage | Missing highlights |
|-----------|-----------|----------|-------------------|
| `tests/cli/` | `cli.py` | 38% | App factory, sub-command wiring |
| | `cli_agent.py` | 34% | Agent entry points |
| | `cli_agent_create.py` | 16% | Create wizard |
| | `cli_bot.py` | 94% | — |
| | `cli_ops.py` | 90% | — |
| | `cli_setup.py` | 76% | Error branches |
| | `cli_voice_smoke.py` | 97% | — |
| `tests/commands/` (none) | `commands/*handlers.py` | **0%** | Completely untested |
| `tests/inbound/` | Overall | **86%** | — |
| | `wire_parser.py` | 0% | Base parser |
| | `wire_parser_discord.py` | 46% | Edge cases |
| | `wire_parser_telegram.py` | 46% | Edge cases |
| | `session_builder.py` | 80% | Error branches |
| `tests/outbound/` | Overall | ~55% | — |
| | `emitter.py` | 55% | Streaming, fallback, edit chains |
| | `error_handler.py` | 97% | — |
| | `formatter.py` | **0%** | No tests exist |
| | `throttle.py` | 100% | — |
| `tests/blobstore/` | Overall | **59%** | — |
| | `serve.py` | 54% | Error paths, auth edge cases |
| | `_handlers.py` | 62% | Blob write errors |
| | `auth.py` | 65% | Auth edge cases |
| | `cli.py` | **0%** | No tests exist |
| `tests/agents/` | Overall | **98%** | — |
| `tests/typing/` | Overall | **96%** | — |

---

### Recommendations (prioritized)

1. **Remove stale RED-phase framing** (Low effort, high signal).
   Six test files still claim tests "MUST FAIL" or "do not yet exist" despite passing for weeks. Stale framing erodes trust in test docs. Update docstrings to match current state.

2. **Add coverage for `src/lyra/commands/*` handlers** (High impact).
   Three command handler modules (`add_vault`, `identity`, `search`) have 0% coverage and no test file exists. They are user-facing command logic; even thin integration tests through `CliRunner` would close the gap.

3. **Create `tests/outbound/test_formatter.py`** (Medium effort, medium impact).
   `outbound/formatter.py` (7 statements) is at 0% coverage. DiscordFormatter and TelegramFormatter are stage-axis components; absence of tests means rendering regressions (MarkdownV2 escaping, truncation) will not be caught.

4. **Replace `asyncio.sleep(0.1)` in `tests/typing/test_integration_nats.py`** (Low effort, prevents flakes).
   Two `sleep(0.1)` calls synchronize NATS publisher-to-listener round-trips. Under CI load or xdist these will flake. Replace with `asyncio.Event` or poll loop with timeout.

5. **Parametrize repetitive test bodies** (Medium effort, maintainability win).
   Router rules (8 methods), emitter smoke tests (Discord 10, Telegram 9), and voice-smoke failure paths (~20) are near-identical copies. Converting to `@pytest.mark.parametrize` would cut ~300–400 lines of test code and make adding new cases trivial.
