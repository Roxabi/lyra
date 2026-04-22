# Code Smells Analysis: core/agent + core/cli

### Summary
The core/agent and core/cli modules contain 13 functions exceeding 50 lines, 1 god class (`AgentBase` with 17 methods), and significant code duplication in spawn/retry logic between streaming and non-streaming CLI pool implementations. Deep nesting (5-7 levels) is prevalent in protocol parsing functions, and long parameter lists (10-15 params) appear in DI constructors and assembly functions.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| `core/cli/cli_non_streaming.py` | 58 | `read_until_result()` - 150 lines, 5-level nesting | High | Extract state machine into smaller handler functions per event type |
| `core/agent/agent_db_loader.py` | 34 | `agent_row_to_config()` - 145 lines | High | Extract field parsers into dedicated builder methods per JSON column |
| `core/cli/cli_streaming_parser.py` | 33 | `parse_line()` - 115 lines, 7-level nesting | High | Create handler dict pattern: `{type: handler_fn}` dispatch |
| `core/agent/agent_seeder.py` | 49 | `_parse_toml()` - 102 lines | Medium | Extract TOML section parsers (voice, patterns, workspaces) |
| `core/cli/cli_pool.py` | 103 | `send()` - 89 lines | Medium | Extract spawn-or-reuse logic into `_get_or_spawn_entry()` helper |
| `core/cli/cli_streaming.py` | 73 | `__anext__()` - 86 lines | Medium | Move timeout/EOF handling to `_read_next_event()` helper |
| `core/cli/cli_pool_streaming.py` | 37 | `send_streaming()` - 79 lines, 5-level nesting | Medium | Extract `_handle_stale_resume()` and `_validate_entry()` helpers |
| `core/cli/cli_pool_worker.py` | 102 | `_spawn()` - 71 lines | Medium | Extract process creation and early-liveness-check into separate methods |
| `core/agent/agent.py` | 38 | `AgentBase` - 17 methods (god class) | Medium | Split into AgentReloader + AgentVoiceHandler mixins |
| `core/agent/agent_builder.py` | 179 | `_assemble_agent()` - 15 params | Medium | Accept kwargs dict or build Agent incrementally via builder pattern |
| `core/agent/agent.py` | 46 | `__init__()` - 12 params | Medium | Bundle dependencies into `AgentDependencies` dataclass |
| `core/cli/cli_pool.py` | 66 | `CliPool.__init__()` - 10 params | Low | Create `CliPoolConfig` dataclass for timeout/buffer settings |
| `core/cli/cli_pool.py` + `cli_pool_streaming.py` | - | DRY violation: duplicate stale-resume retry logic | High | Extract to `_retry_on_stale_resume()` shared helper |
| `core/cli/cli_pool.py` + `cli_pool_streaming.py` | - | DRY violation: duplicate entry validation (alive, prompt, model_config) | High | Extract `_validate_or_respawn_entry()` shared helper |
| `core/agent/agent_db_loader.py` + `agent_seeder.py` | - | DRY violation: similar JSON field parsing patterns | Medium | Create shared `parse_json_fields()` helper with field mapping |

### Metrics

- **Total files analyzed:** 22 (12 agent + 10 cli)
- **Total lines:** 3,351
- **Functions > 50 lines:** 13
- **Max function length:** 150 lines (`read_until_result`)
- **Avg function length (long funcs):** 89 lines
- **Classes > 300 lines:** 0
- **God classes (>=10 methods):** 1 (`AgentBase` - 17 methods)
- **Long parameter lists (>5 params):** 5 functions
- **Deep nesting (>4 levels):** 4 functions
- **Duplication hotspots:** 3 (stale-resume retry, entry validation, JSON parsing)

### Recommendations

1. **High Priority - Extract shared helpers for stale-resume logic**
   - Create `_retry_on_stale_resume(fn, pool_id, entry)` wrapper in `cli_pool_worker.py`
   - Both `send()` and `send_streaming()` use identical 2-attempt retry loop

2. **High Priority - Refactor protocol parsing state machines**
   - `read_until_result()` and `parse_line()` share event-dispatch pattern
   - Replace nested if/elif chains with handler dict: `_HANDLERS[msg_type](data)`
   - Reduces nesting from 5-7 levels to 2-3

3. **High Priority - Extract JSON field parsing helpers**
   - `agent_row_to_config()` and `_parse_toml()` duplicate `json.loads(x) if x else default` pattern
   - Create `_parse_json_field(value, default)` helper

4. **Medium Priority - Split AgentBase into mixins**
   - `AgentReloaderMixin`: reload config, plugins, router rebuild
   - `AgentVoiceMixin`: voice command handling, TTS/STT wiring
   - Core `AgentBase` would have ~8 methods remaining

5. **Medium Priority - Bundle constructor parameters**
   - `CliPoolConfig` dataclass for timeout/buffer CLI pool settings
   - `AgentDependencies` dataclass for DI container passed to `AgentBase.__init__`

6. **Low Priority - Add noqa suppression documentation**
   - Existing `# noqa` comments are justified (DI constructors, protocol functions)
   - Add brief rationale comments: `# noqa: PLR0913 — DI constructor, unavoidable`
