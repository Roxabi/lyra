# Architecture Analysis: core/commands, core/stores, core/pool, core/messaging

### Summary

The core/commands, core/stores, core/pool, and core/messaging areas show a layered architecture with messaging as the deepest layer (no external dependencies), but contain **4 critical layer violations** where core/stores imports infrastructure layer directly. Multiple circular dependency workarounds using dynamic imports indicate architectural coupling issues. The commands and pool modules suffer from God Object anti-patterns with excessive constructor parameters.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| stores/thread_store.py | 23 | Layer violation: imports `lyra.infrastructure.stores.sqlite_base` | **Critical** | Move ThreadStore to `lyra.infrastructure.stores/` (per ADR-048 pattern) |
| stores/prefs_store.py | 10 | Layer violation: imports `lyra.infrastructure.stores.sqlite_base` | **Critical** | Move PrefsStore to `lyra.infrastructure.stores/` |
| stores/message_index.py | 13 | Layer violation: imports `lyra.infrastructure.stores.sqlite_base` | **Critical** | Move MessageIndex to `lyra.infrastructure.stores/` |
| stores/pairing.py | 21 | Layer violation: imports `lyra.infrastructure.stores.sqlite_base` | **Critical** | Move PairingManager to `lyra.infrastructure.stores/` |
| commands/command_router.py | 108 | Circular dependency workaround: `importlib.import_module("lyra.core.processors")` | Medium | Extract processor registration to separate module |
| commands/builtin_commands.py | 67, 184, 215 | Circular dependency workarounds: dynamic imports of `runtime_config` | Medium | Create shared config accessor protocol |
| pool/pool_processor_exec.py | 135 | Circular dependency workaround: `importlib.import_module("lyra.core.processors")` | Medium | Extract processor registry to messaging layer |
| pool/pool.py | 283 | Circular dependency workaround: `from ..memory import SessionSnapshot` | Low | Acceptable - late binding for optional feature |
| commands/command_router.py | 46-62 | God Object: 16-parameter constructor (PLR0913 suppressed) | High | Split into CommandDispatcher, WorkspaceManager, PluginCoordinator |
| pool/pool.py | 32-44 | SRP violation: 10-parameter constructor managing session, voice, workspace, history, debouncing | Medium | Extract VoiceModeMixin, WorkspaceMixin, SessionMixin |
| stores/prefs_store.py | 13 | TYPE_CHECKING import from infrastructure (acceptable) | Info | Pattern is correct for DI |
| stores/pairing.py | 18 | TYPE_CHECKING import from infrastructure (acceptable) | Info | Pattern is correct for DI |
| pool/pool_observer.py | 8 | TYPE_CHECKING import from infrastructure (acceptable) | Info | Pattern is correct for DI |

### Metrics

- **Module coupling: 5/10** (average across all areas)
  - messaging: 1/10 (excellent - foundational)
  - stores: 7/10 (high - layer violations)
  - pool: 5/10 (medium - dynamic imports)
  - commands: 7/10 (high - many dependencies)
- **Circular deps: 5** (all worked around via dynamic imports)
- **Layer violations: 4** (all in core/stores)

### Dependency Direction

```
Expected:  infrastructure → core → (no further deps)
Actual:     core/stores → infrastructure (VIOLATION)
```

**messaging layer**: Correctly positioned as deepest core layer (no external deps) ✓

**stores layer**: VIOLATES layering - SQLite implementations remain in core despite ADR-048 stating they moved to infrastructure

**pool layer**: Acceptable - uses TYPE_CHECKING for infrastructure types, dynamic imports for processors

**commands layer**: High coupling - depends on messaging, pool, runtime_config, smart_routing_protocol, processors

### Recommendations

1. **Critical - Complete ADR-048 Migration**: Move `ThreadStore`, `PrefsStore`, `MessageIndex`, and `PairingManager` to `lyra.infrastructure.stores/`. Keep only protocols and factory functions in `core/stores/` (as `__init__.py` already documents).

2. **High - Refactor CommandRouter**: Split the 16-parameter constructor into:
   - `CommandDispatcher` (core routing logic)
   - `WorkspaceManager` (folder/workspace commands)
   - `PluginCoordinator` (plugin loading lifecycle)

3. **Medium - Extract ProcessorRegistry**: Move processor registry to `core/messaging/` to break circular dependency between commands → processors and pool → processors. Processors are message handlers and belong in messaging layer.

4. **Medium - Create RuntimeConfigProtocol**: Instead of dynamic imports in `builtin_commands.py`, define a protocol in `core/` that `RuntimeConfigHolder` implements. Commands depend on the protocol, not the concrete implementation.

5. **Low - Pool Decomposition**: Consider extracting `VoiceModeMixin` and `WorkspaceMixin` from Pool to reduce the 10-parameter constructor. Pool's core responsibility is turn serialization and debouncing.
