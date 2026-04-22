# Architecture Analysis: core/agent + core/cli

### Summary

The `core/agent` and `core/cli` modules exhibit a generally clean architecture with well-decomposed mixins in CLI, but suffer from **layer violations** where core imports from infrastructure. The `agent.py` module has moderate coupling with 11 sibling imports and mixed responsibilities. CLI modules demonstrate excellent separation via the mixin pattern.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| `/home/mickael/projects/lyra/src/lyra/core/agent/agent.py` | 12 | Layer violation: imports `AgentStore` from `infrastructure.stores` | High | Use `AgentStoreProtocol` from core or dependency injection |
| `/home/mickael/projects/lyra/src/lyra/core/agent/agent_refiner.py` | 16 | Layer violation: imports `AgentStore` from `infrastructure.stores` | High | Accept `AgentStoreProtocol` in constructor |
| `/home/mickael/projects/lyra/src/lyra/core/cli/cli_pool.py` | 17 | Layer violation: imports `TurnStore` from `infrastructure.stores` | High | Define `TurnStoreProtocol` in core or use DI |
| `/home/mickael/projects/lyra/src/lyra/core/cli/cli_pool_session.py` | 13 | Layer violation: imports `TurnStore` from `infrastructure.stores` | High | Define protocol in core/stores |
| `/home/mickael/projects/lyra/src/lyra/core/agent/agent.py` | 46-81 | SRP violation: `__init__` has 12 parameters mixing concerns | Medium | Extract configuration/composition into factory |
| `/home/mickael/projects/lyra/src/lyra/core/agent/agent_db_loader.py` | 34 | High complexity: `agent_row_to_config` (C901, PLR0915) | Medium | Break into smaller parser functions |
| `/home/mickael/projects/lyra/src/lyra/core/agent/agent.py` | 19-26 | Coupling: 11 sibling module imports | Medium | Consider facade pattern for common dependencies |
| `/home/mickael/projects/lyra/src/lyra/core/cli/cli_non_streaming.py` | 30,69 | Circular import avoidance: local imports from `cli_pool` | Low | Acceptable pattern for circularity prevention |
| `/home/mickael/projects/lyra/src/lyra/core/cli/cli_streaming.py` | 41,192 | Circular import avoidance: local imports from `cli_pool` | Low | Acceptable pattern for circularity prevention |
| `/home/mickael/projects/lyra/src/lyra/core/agent/agent_config.py` | 9 | Cross-subdir import: imports `CommandConfig` from `commands` | Low | Consider moving shared types to `core/types.py` |
| `/home/mickael/projects/lyra/src/lyra/core/agent/agent_builder.py` | 126 | Local import to avoid cycle: imports `CommandConfig` locally | Low | Acceptable but indicates structural tension |

### Metrics

- **Module coupling:** 6/10 (agent.py has 11 sibling imports; CLI well-decomposed)
- **Circular deps:** 0 true cycles (3 avoided via local imports in CLI)
- **Layer violations:** 4 (all core → infrastructure imports)
- **SRP violations:** 2 (agent.py, agent_db_loader.py)
- **Total lines:** 3,351 across both areas

### Recommendations

1. **High Priority:** Define `TurnStoreProtocol` and `AgentStoreProtocol` in `core/stores/` and use TYPE_CHECKING imports in core modules. Infrastructure implementations should depend on core protocols, not vice versa.

2. **High Priority:** Replace direct `AgentStore` imports in `agent.py` and `agent_refiner.py` with protocol-based dependency injection via constructor parameters.

3. **Medium Priority:** Extract agent factory logic from `agent.py.__init__` into a dedicated `AgentFactory` class. The 12-parameter constructor indicates mixed initialization concerns.

4. **Medium Priority:** Decompose `agent_db_loader.py:agent_row_to_config()` into smaller functions (parse_voice_config, parse_workspaces, parse_patterns, etc.) to reduce cyclomatic complexity.

5. **Low Priority:** Consider extracting shared config types (`CommandConfig`, `ModelConfig`) to a `core/config_types.py` to reduce cross-subdir dependencies.

6. **Low Priority:** Document the local import pattern used in `cli_non_streaming.py` and `cli_streaming.py` as intentional circular dependency prevention.
