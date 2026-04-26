# src/lyra/infrastructure/ — Persistence Layer

Infrastructure layer for persistence implementations. Per ADR-048, SQLite store
implementations live here while protocols remain in `lyra.core.stores/`.

## Layer ordering

```
lyra.core (protocols) ← lyra.llm | lyra.nats ← lyra.infrastructure (implementations) ← lyra.adapters ← lyra.bootstrap
```

## Files (pending migration)

All files will be moved from `src/lyra/core/stores/`:

| File | Status | Destination |
|------|--------|-------------|
| `sqlite_base.py` | **migrated** | `infrastructure/stores/sqlite_base.py` |
| `agent_store.py` | **migrated** | `infrastructure/stores/agent_store.py` |
| `agent_store_migrations.py` | pending | `infrastructure/stores/agent_store_migrations.py` |
| `auth_store.py` | **migrated** | `infrastructure/stores/auth_store.py` |
| `credential_store.py` | pending | `infrastructure/stores/credential_store.py` |
| `identity_alias_store.py` | pending | `infrastructure/stores/identity_alias_store.py` |
| `message_index.py` | **migrated** | `infrastructure/stores/message_index.py` |
| `pairing.py` | **migrated** | `infrastructure/stores/pairing.py` |
| `prefs_store.py` | **migrated** | `infrastructure/stores/prefs_store.py` |
| `thread_store.py` | **migrated** | `infrastructure/stores/thread_store.py` |
| `turn_store.py` | **migrated** | `infrastructure/stores/turn_store.py` |
| `turn_store_queries.py` | **migrated** | `infrastructure/stores/turn_store_queries.py` |
| `turn_store_session.py` | **migrated** | `infrastructure/stores/turn_store_session.py` |

## Files staying in core/stores

These files have zero infrastructure dependencies and remain in `lyra.core.stores/`:

- `agent_store_protocol.py` — `AgentStoreProtocol` + `make_agent_store()` factory
- `json_agent_store.py` — test double for `AgentStore` (in-memory + JSON)
- `pairing_config.py` — pure Pydantic dataclass, no DB logic

## Governance rule

Any new subdirectory under `infrastructure/` (e.g., `infrastructure/telemetry/`,
`infrastructure/fs/`, `infrastructure/cache/`) requires its own ADR.
Adding files to existing subdirectories does not.
