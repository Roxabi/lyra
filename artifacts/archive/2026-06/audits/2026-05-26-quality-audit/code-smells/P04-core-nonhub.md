## P04 core (non-hub) Code Smell Audit — 2026-05-26

### Summary
- 20 functions exceed cognitive-complexity threshold (4.4% of 457 functions); the worst offender (`runtime_config.set_param`) scores ~124.
- 5 files exceed 300 lines — all carry valid exemptions; no new violations.
- 1 clear DRY violation: `CliPool.send` and `CliPoolStreamingMixin.send_streaming` duplicate ~40 lines of spawn/respawn/stale-resume logic.

### Findings

| File | Line | Severity | Description | Recommendation |
|---|---|---|---|---|
| `src/lyra/core/runtime_config.py` | 176 | Critical | `set_param` is 124 lines, CC~124. Monolithic if/elif chain handling every config key with validation + coercion. | Split into per-key `_set_{key}` helpers; dispatch via dict or match. |
| `src/lyra/core/cli/cli_non_streaming.py` | 57 | High | `read_until_result` is 148 lines, CC~81. Nested timeout/idle-retry/EOF/JSON-decode/result-dispatch logic in one loop. | Extract state machine: `_read_next_line`, `_handle_timeout`, `_handle_result`. |
| `src/lyra/core/pool/pool_processor_exec.py` | 104 | High | `process_one` is 160 lines, CC~62. Mixes processor pre/post hooks, streaming vs non-streaming dispatch, session-id update, and compaction trigger. | Slice into `_run_pre_processor`, `_run_streaming_dispatch`, `_run_non_streaming_dispatch`. |
| `src/lyra/core/tts_dispatch.py` | 172 | High | `synthesize_and_dispatch_audio` is 119 lines, CC~42. Resolves prefs, language, voice, synthesizes, dispatches, and handles errors all inline. | Extract `resolve_language`, `resolve_voice`, `build_notification` helpers. |
| `src/lyra/core/pool/pool_processor.py` | 36 | High | `PoolProcessor.process_loop` CC~45; `_process_with_cancel` CC~31. Cancel-in-flight race logic intertwined with debounce merge. | Move cancel-in-flight racing to a dedicated `_race_agent_vs_inbox` helper. |
| `src/lyra/core/cli/cli_pool.py` | 111 | High | `CliPool.send` is 107 lines, CC~37. Spawn/respawn/stale-resume logic duplicated with streaming variant. | Extract `_ensure_entry_alive` helper shared with `send_streaming`. |
| `src/lyra/core/cli/cli_pool_streaming.py` | 38 | High | `CliPoolStreamingMixin.send_streaming` is 106 lines, CC~37. Near-identical spawn/respawn block to `CliPool.send` (DRY). | Share `_ensure_entry_alive` helper; unify divergence via callback/strategy. |
| `src/lyra/core/cli/cli_pool_worker.py` | 136 | High | `CliPoolWorkerMixin._spawn` is 104 lines, CC~22. Command building, env filtering, early-liveness check, audit emit in one flow. | Extract `_build_env`, `_early_liveness_check`, `_emit_audit_spawn`. |
| `src/lyra/core/agent/agent_db_loader.py` | 34 | High | `agent_row_to_config` is 148 lines, CC~28. Sequential deserialization of every JSON column with ad-hoc validation. | Compose via `_load_voice`, `_load_workspaces`, `_load_smart_routing` helpers. |
| `src/lyra/core/processors/stream_processor.py` | 178 | High | `StreamProcessor.process` is 117 lines, CC~19; class has 17 methods (>5 concerns: text, reasoning, tool, error, state machine). | Decompose into `TextBlockHandler`, `ToolBlockHandler`, `ErrorHandler` mixins or delegates. |
| `src/lyra/core/cli/cli_streaming.py` | 72 | Medium | `StreamingIterator.__anext__` CC~30. Event loop races CLI stdout against timeout/EOF. | Extract `_read_next_event` and `_handle_stream_terminal_state`. |
| `src/lyra/core/cli/cli_streaming_parser.py` | 301 | Medium | `CliStreamingParser._handle_content_block_delta` CC~30. | Extract sub-handlers per block type (text_delta, input_json, thinking). |
| `src/lyra/core/persona.py` | 21 | Medium | `compose_system_prompt_from_json` CC~25. Builds paragraphs via nested ifs for every persona field. | Use a declarative `ParagraphBuilder` loop over field definitions. |
| `src/lyra/core/commands/command_router.py` | 166 | Medium | `CommandRouter._build_builtin_handlers` CC~20; class has 11 methods spanning routing, builtins, session commands, passthroughs, metadata. | Split builtins table to a separate `BuiltinCommandRegistry`. |
| `src/lyra/core/commands/command_router.py` | 236 | Medium | `CommandRouter.dispatch` CC~18. Layers builtin → session → plugin → passthrough dispatch. | Extract `_dispatch_plugin` and `_dispatch_session` helpers. |
| `src/lyra/core/session_lifecycle.py` | 115 | Medium | `SessionManager._run_concept_extraction` CC~21; `_run_preference_extraction` CC~16. Nearly identical validation loops (DRY). | Extract `_validate_and_upsert_items` shared helper. |
| `src/lyra/core/commands/builtin_commands.py` | 42 | Medium | `help_command` CC~25. Aggregates builtins + session + plugin + passthrough descriptions. | Build description list via composable `DescriptionSource` objects. |
| `src/lyra/core/commands/command_loader.py` | 89 | Medium | `CommandLoader.discover` CC~23. Scans filesystem, validates manifests, builds registry. | Extract `_scan_plugins`, `_validate_manifest`, `_register_plugin`. |
| `src/lyra/core/agent/agent_refiner_stages.py` | 20 | Medium | `build_system_prompt` CC~18. Repeats persona-paragraph pattern already in `persona.py`. | Reuse `compose_system_prompt_from_json` or unify prompt builders. |
| `src/lyra/core/tts_dispatch.py` | 172 | Medium | `AudioPipeline` feature envy toward `Hub` — reaches into `self._hub._tts`, `_prefs_store`, `_route_outbound`, `resolve_binding`. | Move TTS dispatch to an outbound stage or inject resolved dependencies instead of `Hub`. |
| `src/lyra/core/agent/agent_commands.py` | 89 | Low | `CommandReloadManager.reload_plugins` CC~16. | Extract `_reload_one_plugin` helper. |
| `src/lyra/core/agent/agent_seeder.py` | 55 | Low | `_parse_toml` is 105 lines, CC~17. TOML row building with one branch per field. | Use a schema-driven row builder (field → converter map). |
| `src/lyra/core/debouncer.py` | 52 | Low | `MessageDebouncer.collect` CC~16. Timeout + drain loop. | Extract `_drain_queue` helper. |
| `src/lyra/core/processors/_scraping.py` | 30 | Low | `_is_private_ip` CC~16. IP regex + CIDR checks. | Keep as-is or split regex vs CIDR logic. |
| `src/lyra/core/stores/json_agent_store.py` | 62 | Low | `JsonAgentStore.connect` CC~16. Loads agents, bot_map, bot_settings in sequence. | Extract `_load_agents`, `_load_bot_map`, `_load_bot_settings`. |

### Metrics

| Metric | Count | % of population |
|---|---|---|
| Files scanned | 99 | — |
| Total functions | 457 | — |
| Total classes | 153 | — |
| Functions >100 lines | 9 | 2.0% |
| Functions with estimated CC >15 | 20 | 4.4% |
| God classes (>5 responsibilities) | 6 | 3.9% |
| Files >300 lines (exempt) | 5 | 5.1% |
| DRY violations (>=3 lines, >=2 files) | 1 major | — |
| Feature envy instances | 1 | — |

**Notes on methodology:** Cognitive complexity is AST-estimated (loops, conditionals, try/except, nesting, nested functions). Exact values may differ from commercial tools by ±10%. All five >300-line files (`cli_pool.py`, `cli_pool_worker.py`, `cli_streaming_parser.py`, `stream_processor.py`, `render_events.py`) are tracked in `tools/file_exemptions.txt` with linked issues; no exemption breach.

### Recommendations (prioritized)

1. **Extract `set_param` dispatcher** (`runtime_config.py`). Split the 124-line if/elif ladder into per-key helpers. This is the single biggest readability and testability win in the partition.
2. **Unify spawn/respawn logic** between `CliPool.send` and `CliPoolStreamingMixin.send_streaming`. Introduce an `_ensure_entry_alive` coroutine (or `EntryResolver` helper) parameterized by error-return vs raise and by model-config mismatch policy.
3. **Slice `process_one` into pipeline stages** (`pool_processor_exec.py`). Separate processor pre/post, streaming dispatch, non-streaming dispatch, and session-id bookkeeping into distinct private methods or a small strategy registry.
4. **Decompose `StreamProcessor`** into per-concern delegates. The class handles text blocks, reasoning blocks, tool calls, error paths, and state-machine management. Delegate to `TextBlockHandler`, `ToolBlockHandler`, `ErrorHandler` to shrink the 17-method surface and lower CC.
5. **Extract shared extraction helper** in `session_lifecycle.py`. `_run_concept_extraction` and `_run_preference_extraction` share identical JSON-parsing and validation loops; a single `_validate_and_upsert_items` helper removes ~20 lines of duplication and lowers both CC scores.
