# CLAUDE.md — roxabi-blobs

## Identity

`roxabi-blobs` is a **standalone Python package** — content-addressed `BlobStore` (Flat-FS + SQLite manifest) for the Roxabi plugin ecosystem. Lives in the Lyra monorepo for colocation with consumers but is versioned independently.

→ Architecture contract: `docs/architecture/adr/067-blobstore-abstraction-flat-fs-content-addressed.mdx`
→ V1 spec: `artifacts/specs/1063-roxabi-blobs-package-spec.mdx`

## Distribution

¬PyPI. Distributed via GitHub source reference (post v0.1.0):

```toml
[tool.uv.sources]
roxabi-blobs = {
  git = "https://github.com/Roxabi/lyra.git",
  subdirectory = "packages/roxabi-blobs",
  tag = "roxabi-blobs/v0.1.0"
}
```

Lyra itself consumes via `{ workspace = true }`.

## Public API (stable contract)

Defined by `__all__` in `src/roxabi_blobs/__init__.py`:

- `BlobStore` — `Protocol` with 4 async methods (`put`/`get`/`exists`/`delete`)
- `FsBlobStore` — concrete impl (Flat-FS + SQLite WAL)
- `BlobRef` — Pydantic envelope (`store_key`, `content_hash`, `mime`, `size`, `filename?`, `source`, `platform_ref?`, `platform_message_id?`, `created_at`)
- `BlobNotFoundError`, `BlobWriteError`, `BlobConsistencyError` — typed errors

`_`-prefixed submodules (`_schema` if any) are internal.

## Usage: ingest_bytes_to_blob_ref

Shared eager-ingest helper for adapter code (Telegram #1065, Discord #1066, future Slack/CLI).
Prevents N×M duplication by centralising the ingest path (ADR-073 three-strikes rule).

```python
from roxabi_blobs import FsBlobStore, ingest_bytes_to_blob_ref

async with FsBlobStore(root) as store:
    ref = await ingest_bytes_to_blob_ref(
        store,
        data,                            # raw bytes
        mime="audio/ogg",
        source="telegram_voice",         # verbatim — no enum validation
        platform_ref="tg:file_id_abc",   # optional
        platform_message_id="msg-42",    # optional
        filename="voice.ogg",            # optional
    )
    # ref.store_key → opaque handle; pass to store.get(ref.store_key)
    # ref.content_hash → sha256 hex
```

- Dedup: same bytes ingested N× → 1 file on FS, N `blob_refs` provenance rows.
- `source` accepts any string — do NOT add an enum here (extensibility invariant).
- Import: `from roxabi_blobs import ingest_bytes_to_blob_ref` or `from roxabi_blobs.ingest import ...`
- Zero `lyra.*` imports — safe to consume from voiceCLI, imageCLI, etc.

## Invariants

- **Write order:** `file → fsync(file) → fsync(shard dir) → INSERT blobs → INSERT blob_refs`. Missing dir-fsync = crash-recovery hole.
- **Concurrency:** `FsBlobStore` holds an `asyncio.Lock` over the entire write path. `BUSY_TIMEOUT=5000` only covers inter-process; in-process serialisation requires the lock.
- **Dedup:** content-addressed by `sha256(data)`. Same blob put N× → 1 row in `blobs`, N rows in `blob_refs`, 1 file on FS.
- **`exists` is deterministic:** `ORDER BY ingested_at DESC LIMIT 1` returns the latest `BlobRef`; the supporting index `(content_hash, ingested_at DESC)` is bootstrapped in `schema.py`.
- **`delete` semantics:** removes one `blob_refs` row; unlinks file + drops `blobs` row only when no remaining refs to that hash. **Not called from production paths in V1** — interface exists so future retention policies don't require schema migration.
- **`BlobRef` ownership:** the canonical envelope lives here. The mirror in `roxabi-contracts` (V2 / #1064) is a wire-side copy with identical field shape; `roxabi-blobs` does **not** import from `roxabi-contracts` (avoids storage ↔ transport cycle).
- **Loss model:** single-host, no replication, loss tolerated (v1). Documented in ADR-067 §Negative.
