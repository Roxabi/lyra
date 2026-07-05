# AGENTS.md — roxabi-satellite

## Role

Shared NATS **satellite plumbing** for GPU worker CLIs. Domain engines stay in
each CLI repo; this package owns blobstore wiring, ingress validation, and
structured error replies.

## Boundary

| Owns | Does NOT own |
|---|---|
| BlobStore singleton (HTTP — → `docs/architecture/storage.md`) | Whisper / Qwen / FLUX / LiteLLM |
| WorkEnvelope coercion | `NatsAdapterBase.handle()` business logic |
| Voice STT/TTS ingress validation | Hub adapters (telegram, discord, …) |
| `WorkerError` reply bytes | Model warmup / registry |

## Dependencies

Depends on all three SDK packages: `roxabi-contracts`, `roxabi-nats`,
`roxabi-blobs`. External CLIs should depend on **`roxabi-satellite` only**
(transitive SDK pull).

## Adding a domain

1. `src/roxabi_satellite/<domain>/validation.py` — contract ingress
2. `src/roxabi_satellite/<domain>/replies.py` — error wire bytes
3. Tests under `packages/roxabi-satellite/tests/`
4. Migrate one CLI at a time (voice first)
