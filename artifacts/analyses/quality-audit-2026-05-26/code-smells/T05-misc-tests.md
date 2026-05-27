# T05 Code Smell Audit — tests/cli, tests/commands, tests/inbound, tests/outbound, tests/blobstore, tests/agents, tests/typing

Date: 2026-05-27
Scope: 34 files, 252 test functions, 2 734 total lines

---

## Summary

- **Coverage holes in commands, wire parsers, and blobstore CLI**: `src/lyra/commands/*` and `src/lyra/blobstore/cli.py` have 0% coverage; `src/lyra/inbound/wire_parser*.py` sits at 0–46%. These are functional gaps, not just missing edge-case tests.
- **Flaky NATS e2e sleep**: The only sleep-based waiting in T05 is `tests/typing/test_integration_nats.py` (two `asyncio.sleep(0.1)` calls), which is inherently racy under CI load.
- **Test file bloat**: Three test files exceed the 300-line quality gate (`test_simple_agent.py` 804 L, `test_voice_smoke.py` 589 L, `test_bot_secret.py` 588 L), making navigation and parallel CI sharding harder.

---

## Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `tests/typing/test_integration_nats.py` | 50 | Medium | `await asyncio.sleep(0.1)` used for NATS pub/sub synchronization. Race-prone under CI jitter. | Replace with event-based sync (asyncio.Event or NATS inbox barrier) or bound retry loop. |
| `tests/typing/test_integration_nats.py` | 55 | Medium | Second `await asyncio.sleep(0.1)` for the same reason. | Same as above. |
| `tests/cli/test_voice_smoke.py` | 347 | Low | `test_all_keywords_accepted` loops manually over `keywords` instead of `@pytest.mark.parametrize`. | Parametrize; keeps failure isolation and pytest reporting. |
| `tests/agents/test_voice_command.py` | 212 | Low | `test_render_audio_wav_uses_send_audio`, `test_render_audio_ogg_uses_send_voice`, `test_render_audio_mpeg_uses_send_audio` are structurally identical (only MIME type and expected bot method differ). | Parametrize by `(mime_type, expected_method)`. |
| `tests/blobstore/test_audit.py` | 88 | Low | `test_blob_audit_event_op_literals` uses a manual `for` loop over 4 literals inside the test body. | Use `@pytest.mark.parametrize("op", [...])`. |
| `tests/blobstore/test_audit.py` | 94 | Low | `test_blob_audit_event_result_literals` uses a manual `for` loop over 5 result codes. | Use `@pytest.mark.parametrize("result", [...])`. |
| `tests/agents/test_simple_agent.py` | 349 | Low | Ticket-id prefixes in test names (`test_t8_reset...`, `test_t9a_switch...`, `test_t10_link...`) make test output opaque without a lookup table. | Rename to descriptive condition-based names (`test_reset_routes_through_cli_pool`, `test_switch_cwd_routes_through_cli_pool`). |
| `tests/agents/test_simple_agent.py` | 499 | Low | `test_t12_configure_pool_idempotent` and `test_t13_no_cli_pool_no_errors` continue the same cryptic prefix pattern. | Rename as above. |
| `tests/agents/test_simple_agent.py` | 592 | Low | `test_t8n_reset_routes_through_nats_driver`, `test_t9an_switch_cwd_routes_through_nats_driver`, `test_t9bn_resume_routes_through_nats_driver` also use ticket prefixes. | Rename as above. |
| `tests/agents/test_simple_agent_stt_cleanup.py`, `tests/agents/test_simple_agent_stt_responses.py`, `tests/agents/test_simple_agent_stt_text.py` | 28, 30, 29 | Medium | All three files declare `class TestSimpleAgentAudioBranch` — duplicate class names make JUnit / pytest reports and IDE test runners ambiguous. | Rename per file focus: `TestAudioCleanup`, `TestAudioResponses`, `TestAudioTextPipeline`. |
| `tests/outbound/test_emitter_discord_smoke.py` | 217 | Low | `adapter._resolve_channel` is mocked (a private method of the SUT) to drive `send_streaming()`. | Prefer injecting the channel dependency at construction time or use a higher-level seam so the test does not know private method names. |
| `tests/outbound/test_emitter_telegram_smoke.py` | 152 | Low | `adapter.bot` is replaced with a hand-rolled `AsyncMock` after construction — the adapter is partially real, partially mocked. | Document why the seam is post-construction; consider a `bot_factory` parameter. |
| `tests/cli/test_bot_secret.py` | 495 | Low | `TestE2EV1RedGate` asserts on `Makefile` contents (build-system coupling). A Makefile refactor unrelated to secrets could break this test. | Move build-system contract assertions to `tests/scripts/` or a dedicated build-verification suite. |
| `tests/agents/test_simple_agent.py` | 1 | Medium | File is 804 lines (2.7x the 300-line gate). It covers process, streaming, CLI lifecycle, NATS lifecycle, voice rewrite, empty reply, and backend health. | Split into domain files: `test_simple_agent_process.py`, `test_simple_agent_lifecycle_cli.py`, `test_simple_agent_lifecycle_nats.py`, `test_simple_agent_voice.py`. |
| `tests/cli/test_voice_smoke.py` | 1 | Low | File is 589 lines (1.96x the gate). Contains 15 test methods across 8 classes. | Split into `test_voice_smoke_happy.py` and `test_voice_smoke_failure.py`, or keep as-is with an explicit exemption. |
| `tests/cli/test_bot_secret.py` | 1 | Low | File is 588 lines (1.96x the gate). | Split into `test_secret_install.py`, `test_secret_rm.py`, `test_secret_list.py`. |
| `tests/inbound/test_router.py` | 93 | Low | `test_dm_is_processed`, `test_group_with_mention_is_processed`, `test_group_without_mention_is_dropped` are simple pure-function tests that could be parametrized by `(meta, expected_decision)`. | Parametrize if the router gains more platform rules; acceptable as-is for now. |
| `tests/cli/test_ops_verify.py` | 148 | Low | `_patched_connect(deny)` is inlined in every test (7 times). Minor repetition. | Extract a fixture `patched_connect(deny_per_call)` with a single `patch` context. |

---

## Metrics

| Metric | Value |
|--------|-------|
| Total test files | 34 |
| Total test functions | 252 |
| Total test lines | 2 734 |
| Files > 300 lines | 3 |
| `pytest.mark.parametrize` usages | 4 (underutilized) |
| `asyncio.sleep` / `time.sleep` occurrences | 2 |
| `pytest.mark.skip` / `skipif` | 1 (`test_cross_host.py`) |
| `pytest.mark.xfail` | 1 (`test_serve_api.py:444`) |
| Duplicate test class names | 1 (`TestSimpleAgentAudioBranch` x3) |

### Coverage by partition (source under test)

| Partition | Source coverage | Key gaps |
|-----------|-----------------|----------|
| `tests/inbound` | dispatcher 100%, pipeline 97%, router 100%, session_builder 80% | `wire_parser.py` 0%, `wire_parser_discord.py` 46%, `wire_parser_telegram.py` 46% |
| `tests/outbound` | error_handler 58%, emitter 15% (pulled in via adapter smoke) | `formatter.py` 0%, `throttle.py` 100% (5 stmts only, no dedicated tests) |
| `tests/cli` | cli modules partial | `commands/*` 0% — handlers never exercised |
| `tests/blobstore` | 83% overall | `blobstore/cli.py` 0% |
| `tests/agents` | 98% (`simple_agent.py`) | — |
| `tests/typing` | 96% (`listener.py` 95%) | — |

---

## Recommendations (prioritized)

1. **Add command-handler coverage** — `src/lyra/commands/add_vault/handlers.py`, `identity/handlers.py`, and `search/handlers.py` are at 0%. Add integration tests in `tests/commands/` (the directory does not yet exist) or extend `tests/cli/` to invoke the handlers through the CLI tree.
2. **Fix flaky NATS e2e sleeps** — Replace the two `asyncio.sleep(0.1)` calls in `tests/typing/test_integration_nats.py` with an `asyncio.Event` that the publisher signals or a bounded wait-for assertion (e.g., `wait_for(mgr.start.assert_called_once, timeout=2.0)`).
3. **Split `test_simple_agent.py`** — At 804 lines it is the largest test file in the repo. Split into 3–4 files aligned with the test classes already present (`TestSimpleAgentProcess`, `TestSimpleAgentCliLifecycle`, `TestSimpleAgentNatsLifecycle`, `TestSimpleAgentVoiceRewrite`). Update `tools/file_exemptions.txt` if needed.
4. **Rename duplicate `TestSimpleAgentAudioBranch` classes** — Three identical class names across `tests/agents/test_simple_agent_stt_*.py` produce ambiguous test IDs in CI reports and IDE runners. Rename to reflect file scope.
5. **Parametrize MIME routing in `test_voice_command.py`** — The three `test_render_audio_*` methods are copy-paste identical except for `mime_type` and the expected `bot` method. A single parametrized test reduces maintenance burden when adding new audio formats.
