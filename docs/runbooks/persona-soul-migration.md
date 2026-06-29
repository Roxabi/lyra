# Runbook — Persona / soul blobstore migration

Migrate agent souls from inline `persona_json` (SQLite) to AgentSoul v1 (`soul.md` in blobstore + DB pointers).

**Prerequisites:** `factory-hub` running with blobstore token wired (`factory_blobstore_token`, `init_blobstore()`). Snapshot `config.db` and blobstore **before** any migration step.

## Step A — Backup pair

```bash
mkdir -p /tmp/factory-migration-$(date +%Y%m%d)
sqlite3 ~/.roxabi/factory/config.db ".backup '/tmp/factory-migration-$(date +%Y%m%d)/config.db'"
# Blobstore: see blobstore-backup-restore.md (index first, then shards)
```

## Step B — Schema migration (automatic on hub boot)

Hub applies SQLite migrations adding:

- `soul_meta_json` — envelope only (display_name, memory provision)
- `soul_document_blob_ref` — `sha256:<hex>`
- `soul_document_bytes` — metadata

`persona_json` remains for rollback until staging validates backfill.

## Step C — Backfill persona_json → soul.md

```bash
cd ~/projects/roxabi-factory   # or worktree path
uv run python scripts/backfill_soul_documents.py --dry-run   # inspect
uv run python scripts/backfill_soul_documents.py               # apply
```

Idempotent: agents already having `soul_document_blob_ref` are skipped. Logs report migrated / skipped / errors.

## Step D — Verify SQL

```bash
sqlite3 ~/.roxabi/factory/config.db "
SELECT name,
       CASE WHEN soul_document_blob_ref IS NOT NULL THEN 'blob' ELSE 'inline' END AS soul_source,
       length(persona_json) AS persona_len,
       soul_document_bytes
FROM agents
ORDER BY name;
"
```

Expect: prod agents with non-empty `persona_json` now have `soul_source=blob` and non-null `soul_document_blob_ref`.

## Step E — Hub smoke

```bash
factory agent show <name>    # soul ref + meta visible
journalctl --user -u factory-hub -n 50 | grep -i soul || true
```

New conversations should receive composed `system_prompt` from blob cache. Active sessions keep prior soul until `/reset` or new pool (see persona-soul-operator.md).

## Rollback pointer

If backfill misbehaves: restore `config.db` from Step A backup. Blob orphans are safe; DB ref is authoritative. See persona-soul-rollback.md.