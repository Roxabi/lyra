# src/lyra/infrastructure/ — Persistence Layer

## ADR-048 invariant

Protocols → `lyra.core.stores/` | Implementations → `lyra.infrastructure.stores/`

Never place a SQLite or I/O implementation in `core/`; never place a Protocol in `infrastructure/`.
Stores impl ⊂ infrastructure, protocols ⊂ core/stores. Past migration history in git log.

## Layer ordering

```
lyra.core (protocols) ← lyra.llm | lyra.nats ← lyra.infrastructure (implementations) ← lyra.adapters ← lyra.bootstrap
```

## Subdirectories

| Subdir | Contents | ADR |
|--------|----------|-----|
| `stores/` | SQLite store implementations | ADR-048 |
| `audit/` | `JetStreamAuditSink` — publishes `SecurityEvent` to NATS JetStream | ADR-057 |

## Governance rule

Any new subdirectory under `infrastructure/` (e.g., `infrastructure/telemetry/`,
`infrastructure/fs/`, `infrastructure/cache/`) requires its own ADR.
Adding files to existing subdirectories does not.
