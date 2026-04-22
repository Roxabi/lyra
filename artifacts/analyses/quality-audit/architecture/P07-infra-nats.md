# Architecture Analysis: Infrastructure + NATS

### Summary
The infrastructure and NATS layers exhibit **significant layer violations**, with infrastructure stores directly importing from `lyra.core` (violating the documented layer ordering) and core importing back from infrastructure (creating bidirectional coupling). The ADR-048 migration from `core.stores` to `infrastructure.stores` appears incomplete, as infrastructure stores still depend on core for models, schemas, and migrations that should have been moved or abstracted.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/agent_store.py` | 8-22 | **Layer Violation**: Infrastructure imports from `lyra.core.agent.agent_models`, `lyra.core.agent.agent_schema`, `lyra.core.agent.agent_seeder`, `lyra.core.stores.agent_store_migrations` | High | Move models/schemas/migrations to infrastructure or create protocol interfaces in core |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/auth_store.py` | 10 | **Layer Violation**: Infrastructure imports `TrustLevel` from `lyra.core.auth.trust` | Medium | Move TrustLevel to shared domain or create protocol |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/bot_agent_map.py` | 9-10 | **Layer Violation**: Infrastructure imports from `lyra.core.agent.agent_models`, `lyra.core.agent.agent_schema` | High | Move DDL/DML constants and helpers to infrastructure |
| `/home/mickael/projects/lyra/src/lyra/core/stores/message_index.py` | 13 | **Layer Violation**: Core imports from `lyra.infrastructure.stores.sqlite_base` | High | Relocate to infrastructure or use dependency injection |
| `/home/mickael/projects/lyra/src/lyra/core/stores/prefs_store.py` | 10-13 | **Layer Violation**: Core imports from infrastructure | High | Relocate to infrastructure |
| `/home/mickael/projects/lyra/src/lyra/core/stores/thread_store.py` | 23 | **Layer Violation**: Core imports from infrastructure | High | Relocate to infrastructure |
| `/home/mickael/projects/lyra/src/lyra/core/stores/pairing.py` | 18-21 | **Layer Violation**: Core imports from infrastructure | High | Relocate to infrastructure |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/agent_store.py` | 32-276 | **Single Responsibility**: 244 lines with 4+ responsibilities (agent CRUD, bot mapping delegation, runtime state, TOML seeding) | Medium | Split into AgentStore, BotMappingDelegator, AgentRuntimeStore |
| `/home/mickael/projects/lyra/src/lyra/nats/nats_image_client.py` | 66-115 | **Single Responsibility**: Domain models (ImageRequest, ImageResponse, ImageHeartbeat) defined inline in client file | Low | Move to `roxabi_contracts.image` package |
| `/home/mickael/projects/lyra/src/lyra/nats/nats_tts_client.py` | 35 | **Layer Concern**: TYPE_CHECKING import from `lyra.core.agent.agent_config` | Low | Acceptable as type-only, but consider moving config types to shared layer |
| `/home/mickael/projects/lyra/src/lyra/infrastructure/stores/turn_store.py` | 19-21 | **Coupling**: Imports `TurnStoreSessionMixin` and query functions from sibling modules | Low | Acceptable - proper module decomposition |

### Metrics

- **Module coupling**: 7/10 (High bidirectional coupling between core and infrastructure)
- **Circular dependencies**: 0 (No import cycles, but bidirectional dependencies exist)
- **Layer violations**: 10+ (Major issue - core/infrastructure have mutual imports)

### Recommendations

1. **[P1] Complete ADR-048 Migration**
   - Move remaining files from `lyra.core.stores` to `lyra.infrastructure.stores`: `message_index.py`, `prefs_store.py`, `thread_store.py`, `pairing.py`
   - This eliminates core-to-infrastructure imports

2. **[P1] Break Infrastructure-to-Core Dependencies**
   - Move `agent_schema.py` (DDL/DML constants) to `infrastructure/stores/`
   - Move `agent_models.py` dataclasses to `infrastructure/stores/` or create a shared `lyra.domain` package
   - Move `agent_store_migrations.py` to `infrastructure/stores/`
   - Move `TrustLevel` enum to a shared location or use string literals with validation

3. **[P2] Apply Single Responsibility to AgentStore**
   - Extract `AgentRuntimeState` methods to separate class
   - Bot mapping methods are already delegated to `BotAgentMapStore` - ensure clean delegation

4. **[P3] Extract Domain Models from NATS Clients**
   - Move `ImageRequest`, `ImageResponse`, `ImageHeartbeat` to `roxabi_contracts.image`
   - Follow pattern established by `roxabi_contracts.voice` for STT/TTS

5. **[P3] Document Layer Boundaries**
   - Create `src/lyra/nats/CLAUDE.md` with layer ordering and import rules
   - Add lint rule to flag `from lyra.core` imports in infrastructure layer
