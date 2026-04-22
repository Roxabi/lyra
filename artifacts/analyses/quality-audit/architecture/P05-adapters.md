# Architecture Analysis: Adapters Area

### Summary

The adapters area follows a well-structured decomposition pattern with clear separation between Discord, Telegram, NATS, and shared concerns. However, there is one significant layer violation where shared imports from nats, and several files have high complexity scores. The overall dependency direction is correct (adapters → core), but the shared → nats dependency breaks the expected hierarchy.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| `/home/mickael/projects/lyra/src/lyra/adapters/shared/_shared_streaming_state.py` | 14 | **Layer Violation**: imports `StreamChunkTimeout` from `lyra.adapters.nats.nats_stream_decoder` | High | Move `StreamChunkTimeout` to core.exceptions or create a shared exceptions module |
| `/home/mickael/projects/lyra/src/lyra/adapters/discord/adapter.py` | 64-107 | **SRP Concern**: 270-line facade with 14 injected dependencies | Medium | Consider extracting configuration into a separate dataclass |
| `/home/mickael/projects/lyra/src/lyra/adapters/telegram/telegram.py` | 88-141 | **SRP Concern**: 284-line facade mixing FastAPI routes, aiogram setup, and adapter logic | Medium | Extract route registration to separate module |
| `/home/mickael/projects/lyra/src/lyra/adapters/discord/discord_inbound.py` | 29 | **Complexity**: C901 cyclomatic complexity (noted with noqa) | Medium | Extract DM session wiring to helper function |
| `/home/mickael/projects/lyra/src/lyra/adapters/discord/discord_outbound.py` | 152 | **Complexity**: C901 cyclomatic complexity in `build_streaming_callbacks` | Low | Already well-structured with clear closures |
| `/home/mickael/projects/lyra/src/lyra/adapters/telegram/telegram_outbound.py` | 161 | **Complexity**: C901 cyclomatic complexity in `build_streaming_callbacks` | Low | Mirror pattern of discord_outbound, acceptable |
| `/home/mickael/projects/lyra/src/lyra/adapters/shared/_shared_streaming_emitter.py` | - | **File Size**: ~300 lines (at repo limit) | Low | Consider splitting into smaller focused modules |
| `/home/mickael/projects/lyra/src/lyra/adapters/nats/nats_outbound_listener.py` | 40-68 | **SRP Concern**: 12 instance variables tracking different concerns | Low | Document as intentional aggregation point |

### Metrics

- **Module coupling**: 6/10 (moderate - cross-module imports within adapters, one layer violation)
- **Circular deps**: 0 (all TYPE_CHECKING imports used correctly)
- **Layer violations**: 1 (shared → nats import)
- **Core imports**: 55 (correct direction - adapters depend on core)
- **Infrastructure imports**: 0 (correct - no infrastructure coupling)
- **TYPE_CHECKING guards**: 21 (good practice - prevents runtime circular deps)

### Dependency Graph

```
Expected hierarchy:                    Actual hierarchy:
┌─────────────────┐                    ┌─────────────────┐
│   adapters/     │                    │   adapters/     │
│  ┌───────────┐  │                    │  ┌───────────┐  │
│  │ discord/  │  │                    │  │ discord/  │  │
│  │ telegram/ │  │                    │  │ telegram/ │  │
│  │   nats/   │  │                    │  │   nats/   │←─┼─ layer violation
│  │  shared/  │  │                    │  │  shared/  │  │
│  └────┬──────┘  │                    │  └────┬──────┘  │
└───────┼─────────┘                    └───────┼─────────┘
        │                                      │
        ▼                                      ▼
┌───────────────┐                      ┌───────────────┐
│     core/     │                      │     core/     │
└───────────────┘                      └───────────────┘
```

### Recommendations

1. **High Priority**: Move `StreamChunkTimeout` from `adapters/nats/nats_stream_decoder.py` to `core/exceptions.py` or create `core/messaging/stream_exceptions.py`. This eliminates the layer violation where shared depends on nats.

2. **Medium Priority**: Extract adapter configuration into dedicated dataclasses (e.g., `AdapterDeps` dataclass) to reduce the number of constructor parameters in DiscordAdapter and TelegramAdapter.

3. **Medium Priority**: Extract Telegram route registration (`_register_routes`) to a separate `telegram_routes.py` module to reduce facade file size.

4. **Low Priority**: Document the intentionally aggregated state in `NatsOutboundListener` as a design decision rather than an SRP violation.

5. **Low Priority**: Consider creating a `core/streaming/` package for streaming-related exceptions and state classes that both shared and nats can import without layer violations.

### Positive Observations

- Clean facade pattern: `adapter.py` files are thin delegates to submodules
- Consistent naming convention: `{platform}_{concern}.py` pattern
- Proper TYPE_CHECKING usage throughout to prevent runtime circular imports
- No imports from infrastructure layer (correct layer boundary)
- Shared helpers properly extract common patterns (TypingTaskManager, push_to_hub_guarded)
- OutboundAdapterBase provides clean abstract contract with concrete streaming implementation
