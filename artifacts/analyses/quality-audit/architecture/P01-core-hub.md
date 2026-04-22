# Architecture Analysis: core/hub

### Summary

The core/hub area demonstrates a well-structured decomposition with 30 modules organized around the Hub central coordinator. The architecture follows clean separation patterns with mixin decomposition and extracted outbound dispatchers. However, there are documented circular dependencies between middleware and hub modules, infrastructure types imported in TYPE_CHECKING blocks, and some SRP violations in the STT middleware. The overall architecture is solid but has room for improvement in dependency management.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| middleware_guards.py | 111 | Circular dependency with hub (runtime import of RoutingKey) | Medium | Move RoutingKey to separate protocol file to break cycle |
| hub.py | 34-35 | Infrastructure layer imports (IdentityAliasStore, TurnStore) in TYPE_CHECKING | Low | Acceptable for type hints; consider protocol abstraction if boundary tightening needed |
| hub_registration.py | 15-16 | Infrastructure layer imports in TYPE_CHECKING | Low | Same as above |
| hub_shutdown.py | 13 | Infrastructure layer import (TurnStore) in TYPE_CHECKING | Low | Same as above |
| middleware_stt.py | 144 | Runtime cross-package import from lyra.stt | Info | Acceptable - STT is service layer, not infrastructure |
| middleware_stt.py | 81-200 | SRP violation: transcription + temp file handling + error dispatch + counters | Medium | Extract temp file handling to separate utility; extract counter updates to event emission |
| hub.py | 53-59 | High coupling class (5 mixins inherited) | Medium | Consider composition over inheritance for better testability |
| hub.py | 74-144 | Large __init__ method (70+ lines, 20+ attributes) | Low | Extract factory method or builder pattern |
| _dispatch.py | 27 | C901 (too complex) - function admits complexity in noqa | Low | Consider further decomposition of dispatch_outbound_item |
| outbound_errors.py | 42-87 | Dynamic error classification by module name inspection | Info | Acceptable for avoiding hard dependencies |

### Metrics

- **Module coupling**: 6/10 (hub.py is central with 15+ internal dependencies; mix of tight/loose coupling)
- **Circular deps**: 1 documented, 1 potential (middleware_guards <-> hub via RoutingKey)
- **Layer violations**: 3 (infrastructure imports in TYPE_CHECKING blocks - mitigated but present)
- **Lines of code**: 3,714 total across 30 files (avg 124 lines/file)
- **Largest files**: outbound_router.py (230), outbound_dispatcher.py (223), hub.py (214)

### Recommendations

1. **HIGH PRIORITY**: Break circular dependency between middleware_guards.py and hub.py
   - Move `RoutingKey` from hub_protocol.py (already there) - the import at middleware_guards.py:111 should use `from .hub_protocol import RoutingKey` instead of `from .hub import RoutingKey`
   - This eliminates the need for the `# justified: .hub cycle` comment

2. **MEDIUM PRIORITY**: Extract temp file handling from middleware_stt.py
   - Create `_write_temp_audio()` utility function in a shared module
   - Move `_STT_STAGE_OUTCOMES` counter to event bus pattern for cleaner telemetry

3. **LOW PRIORITY**: Consider composition for Hub
   - The 5-mixin inheritance chain works but makes testing harder
   - A composed Hub with injected components would be more flexible

4. **INFO**: Infrastructure imports in TYPE_CHECKING are acceptable
   - These follow the pattern recommended in CLAUDE.md for type hints
   - No runtime layer violation occurs due to TYPE_CHECKING guard

5. **POSITIVE**: Outbound dispatcher extraction (#760) is well-done
   - `_dispatch.py`, `outbound_audio.py`, `outbound_tts.py`, `outbound_streaming.py` follow SRP
   - AudioDispatch and TtsDispatch are clean helper classes

### Dependency Graph (Simplified)

```
hub.py (central)
  ├── hub_protocol.py (RoutingKey, ChannelAdapter, Binding) <- PURE
  ├── hub_*_mixin.py (5 mixins) <- DECOMPOSED
  ├── pool_manager.py -> Hub (TYPE_CHECKING) <- CYCLE MITIGATED
  ├── outbound_router.py -> outbound_*.py <- WELL DECOMPOSED
  └── middleware.py -> middleware_stages.py <- CLEAN

middleware_guards.py
  └── Runtime import of RoutingKey from .hub <- CYCLE (SHOULD USE hub_protocol)

infrastructure (external)
  └── IdentityAliasStore, TurnStore <- IMPORTED IN TYPE_CHECKING ONLY
```
