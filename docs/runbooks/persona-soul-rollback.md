# Runbook — Persona / soul rollback

Revert soul blobstore migration while keeping the hub operational.

## When to use

- Backfill produced wrong section mapping
- Blobstore ref points to corrupt `soul.md`
- Need immediate return to `persona_json` inline compose path

The loader **falls back** to `persona_json` when soul cache misses or ref is NULL — rollback can be DB-only if `persona_json` was preserved (default during migration).

## Step A — Stop mutating writes

Pause dashboard soul edits and `factory agent patch` on soul fields until rollback completes.

## Step B — Restore config.db (full rollback)

```bash
systemctl --user stop factory-hub || true
cp /path/to/backup/config.db ~/.roxabi/factory/config.db
systemctl --user start factory-hub
```

Verify agents load and conversations work with inline persona.

## Step C — Partial rollback (single agent)

Clear blob pointer; keep `persona_json`:

```bash
sqlite3 ~/.roxabi/factory/config.db "
UPDATE agents
SET soul_document_blob_ref = NULL,
    soul_document_bytes = NULL,
    updated_at = datetime('now')
WHERE name = '<agent_name>';
"
```

Restart not required — ADR-029 hot-reload picks up `updated_at` on next message. Confirm `factory agent show <agent_name>` shows NULL ref.

## Step D — Blobstore orphans

Orphan blobs (no `blob_refs` row or superseded sha) are dedup-safe. Soul refs use `source=soul` and are **exempt** from the 30-day sweep — do not delete active refs manually without clearing DB pointer first.

## Step E — Re-migrate

After fixing root cause, re-run:

```bash
uv run python scripts/backfill_soul_documents.py
```

Pair with persona-soul-migration.md verification queries.