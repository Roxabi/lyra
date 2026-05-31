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

| Subdir / File | Contents | ADR |
|---------------|----------|-----|
| `stores/` | SQLite store implementations | ADR-048 |
| `audit/` | `JetStreamAuditSink` — publishes `SecurityEvent` to NATS JetStream | ADR-057 |
| `turn_writer/` | JetStream subscriber-writer for turns.db (sole writer post-#1331) | ADR-075 |
| `outbound_audio/` | Idempotent JetStream stream + durable consumer + KV provisioning for durable outbound-audio path; hub sole-provisioner for stream+KV, adapters call `ensure_consumer` only | ADR-079 |
| `blobstore_adapter.py` | `HttpBlobStoreAdapter` — satisfies `core.ports.BlobStorePort`; wraps `roxabi_blobs.HttpBlobStore`; owns storage→wire conversion via `BlobRef.from_store_ref` and the empty-store-key guard | ADR-082, ADR-067 |
| `resume_publisher_adapter.py` | `TurnPublisherAdapter` — satisfies `ResumePublisherPort`; delegates publish to NATS-backed `TurnPublisher` and read to SQLite-backed `TurnStore` | ADR-075 |

## BlobStore adapter invariants

`HttpBlobStoreAdapter` is the single seam permitted to import both `roxabi_blobs` (storage) and `roxabi_contracts` (wire). All other lyra modules depend on `BlobStorePort` only. Two invariants are owned here and must not move:

1. **from_store_ref conversion** — `BlobRef.from_store_ref(store_ref)` is the only path from storage→wire BlobRef; no manual field construction anywhere else.
2. **Empty-key guard** — an empty `store_key` after a live `put()` raises `ValueError` immediately (contract violation, not a format check; `store_key` is opaque).

`exists()` maps storage sentinels (`is_sentinel=True`) → `None` because HEAD responses return a sparse sentinel with `content_hash=""` that the wire BlobRef rejects; callers needing the full envelope must `put` (idempotent via content-hash dedup).

## Governance rule

Any new subdirectory under `infrastructure/` (e.g., `infrastructure/telemetry/`,
`infrastructure/fs/`, `infrastructure/cache/`) requires its own ADR.
Adding files to existing subdirectories does not.
