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

## Restore invariant

After restore, the SQLite manifest is authoritative. Shard files on disk **not** referenced by `blobs.store_path` are dedup-orphans — safe to discard.

```bash
sqlite3 index.sqlite "SELECT store_path FROM blobs"   # expected
find . -type f                                         # actual
# discard files in (2) absent from (1)
```

A `blob_refs` row pointing to a **missing** shard is the unrecoverable case — prevented by index-first snapshot order.

→ Write-durability invariant: [architecture/storage.md](../architecture/storage.md) (BlobStore section).