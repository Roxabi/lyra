# Runbook — BlobStore backup & restore

Host path = container path: `~/.roxabi/factory/blobstore` (bind-mounted).

## Backup (order matters)

Snapshot **index first**, then shards — reversing risks phantom `blob_refs` rows.

```bash
mkdir -p /tmp/blobstore-snapshot
sqlite3 ~/.roxabi/factory/blobstore/index.sqlite ".backup '/tmp/blobstore-snapshot/index.sqlite'"
cp -al ~/.roxabi/factory/blobstore /tmp/blobstore-snapshot/blobs
sha256sum -c <(find /tmp/blobstore-snapshot/blobs -type f -exec sha256sum {} +)
```

Off-host: `tar -cf - /tmp/blobstore-snapshot | restic backup --stdin --stdin-filename blobstore.tar`

Recommended cadence: daily, low-traffic window. Store alongside `config.db` backups.

## Soul refs (`source=soul`)

Agent souls (`soul.md`) are stored as immutable blobs referenced from `config.db` (`agents.soul_document_blob_ref`). Backup **must** include both:

1. `config.db` — authoritative pointer (`sha256:…`)
2. Blobstore index + shards — bytes for each active ref

Soul blobs use `source=soul` and are **exempt** from the 30-day `factory-blobstore-sweep` (pinned refs must not disappear while still referenced). After restore, verify:

```bash
sqlite3 ~/.roxabi/factory/config.db \
  "SELECT name, soul_document_blob_ref FROM agents WHERE soul_document_blob_ref IS NOT NULL;"
# Each ref must resolve via blobstore CLI or hub soul.get
```

See also: [persona-soul-migration.md](persona-soul-migration.md), [persona-soul-rollback.md](persona-soul-rollback.md).

## Restore invariant

After restore, the SQLite manifest is authoritative. Shard files on disk **not** referenced by `blobs.store_path` are dedup-orphans — safe to discard.

```bash
sqlite3 index.sqlite "SELECT store_path FROM blobs"   # expected
find . -type f                                         # actual
# discard files in (2) absent from (1)
```

A `blob_refs` row pointing to a **missing** shard is the unrecoverable case — prevented by index-first snapshot order.

→ Write-durability invariant: [architecture/storage.md](../architecture/storage.md) (BlobStore section).