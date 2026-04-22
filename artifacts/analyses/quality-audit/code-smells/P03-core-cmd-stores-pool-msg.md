# Code Smells Analysis: core/commands, core/stores, core/pool, core/messaging

### Summary
The analyzed areas show several code smell patterns: **God classes** (Pool, JsonAgentStore, PoolObserver), **long parameter lists** (up to 14 parameters), and **long functions** (up to 160 lines). The most critical issues are in `pool_processor_exec.py` and `pool.py`. Significant duplication exists in `CommandLoader.load/reload` methods for path validation logic.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| pool_processor_exec.py | 98 | Function `process_one` is 160 lines (PLR0915) | High | Extract streaming path and non-streaming path into separate helper methods |
| pool_processor_exec.py | 30 | Function `guarded_process_one` is 66 lines | Medium | Consider splitting into smaller handlers for timeout/error/cancel paths |
| pool.py | 32 | Constructor has 13 parameters (PLR0913) | High | Extract config object or use builder pattern |
| pool.py | 29 | God class: 22+ methods | High | Extract session management to dedicated SessionManager class |
| pool_observer.py | 16 | God class: 13 methods | Medium | Consider splitting into TurnLogger and SessionObserver |
| pool_observer.py | 92 | Function `log_turn_async` has 9 parameters (PLR0913) | Medium | Bundle params into a context dataclass |
| command_router.py | 46 | Constructor has 14 parameters (PLR0913) | High | Extract config object or use builder/factory pattern |
| command_router.py | 43 | God class: 11 methods | Medium | Acceptable for router; consider extracting session command handling |
| command_router.py | 234 | Function `dispatch` is 64 lines, marked C901 | Medium | Already has noqa comment; consider further decomposition |
| command_loader.py | 132 | Function `load` is 52 lines | Medium | Extract path validation and module loading into separate helpers |
| command_loader.py | 186 | Function `reload` is 50 lines | Medium | Shares validation logic with `load` - extract common helper |
| command_loader.py | 82 | Code duplication: path validation in `load`/`reload` | Medium | Extract `_validate_plugin_paths()` helper |
| pairing.py | 131 | Function `validate_code` is 94 lines | Medium | Extract transaction blocks and auth store upsert into helpers |
| json_agent_store.py | 30 | God class: 17 methods | Medium | Mirrors AgentStore interface; acceptable for test stub |
| builtin_commands.py | 44 | Function `help_command` has 6 parameters (PLR0913) | Low | Internal helper; acceptable with noqa |
| builtin_commands.py | 134 | Function `config_command` has 6 parameters (PLR0913) | Low | Internal helper; acceptable with noqa |
| inbound_bus.py | 35 | Borderline God class: 12 methods | Low | Acceptable for bus implementation |
| pool_processor.py | 36 | Function `process_loop` is 60 lines, marked C901 | Medium | Consider extracting cancel-in-flight logic |
| pool_processor.py | 97 | Function `_process_with_cancel` is 87 lines | Medium | Consider extracting race-handling logic |
| pool_processor_streaming.py | 56 | Function `build_streaming_turn_logger` has 6 parameters | Low | Internal helper; acceptable |

### Metrics

- **Avg function length**: ~28 lines (excluding very short helpers)
- **Max function length**: 160 lines (`process_one` in pool_processor_exec.py)
- **God classes**: 4 (Pool, PoolObserver, JsonAgentStore, LocalBus borderline)
- **Duplication hotspots**: 2 (CommandLoader load/reload validation, Pool backward-compat shims)
- **Long parameter lists**: 6 functions with >5 params (max 14)
- **Deep nesting**: 1 instance (check_rate_limit has 4 levels)

### Recommendations

1. **High Priority**: Refactor `process_one` (160 lines) in `pool_processor_exec.py` - extract streaming and non-streaming paths into dedicated methods.

2. **High Priority**: Introduce `PoolConfig` dataclass to bundle the 13 constructor parameters in `Pool.__init__`.

3. **High Priority**: Introduce `RouterConfig` dataclass to bundle the 14 constructor parameters in `CommandRouter.__init__`.

4. **Medium Priority**: Extract `_validate_plugin_paths()` helper in `CommandLoader` to eliminate duplication between `load` and `reload` methods.

5. **Medium Priority**: Split `Pool` class - extract session lifecycle methods (`reset_session`, `resume_session`, `register_session_callbacks`) into a dedicated `SessionManager` class.

6. **Medium Priority**: Refactor `validate_code` in `PairingManager` - extract the transaction block and auth store upsert into separate private methods.

7. **Low Priority**: Consider introducing `TurnLogContext` dataclass to bundle the 9 parameters in `log_turn_async`.

8. **Low Priority**: Add `# noqa: PLR0913` comments to acceptable long-parameter helpers (`help_command`, `config_command`, `build_streaming_turn_logger`) for consistency.
