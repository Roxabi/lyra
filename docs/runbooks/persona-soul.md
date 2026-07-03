# Runbook — Persona / soul

How soul edits propagate (or do not) across harnesses, plus the one-time
`persona_json` → soul-blobstore migration and its rollback. `persona_json` remains a
loader fallback in code, so the migration/rollback sections stay live.

## Soul is session-scoped

| Action | `Agent.config.system_prompt` | Active session / pool |
|--------|------------------------------|------------------------|
| Save soul in dashboard | Updates on next hub load (ADR-029) | **Unchanged** until new pool |
| `factory agent patch` persona | Same | Same |
| `/reset` or new chat tab | N/A | New session gets current soul |

**Expected lag** — not a defect. Tell operators: after editing soul, start a **new conversation** or `/reset` for immediate effect.

## Edit paths (V1)

| Path | Flow |
|------|------|
| Dashboard `/agents/:name` | PATCH scalars + PUT `soul.md` via BFF → hub `soul.put` |
| CLI scalars | `factory agent patch <name> --json '{"model":"…"}'` — harness/model/voice scalars only |
| CLI soul doc | `scripts/backfill_soul_documents.py` (migration) or hub NATS `soul.put` via RPC client — no direct `factory agent patch` on soul markdown V1 |
| Legacy | `persona_json` inline — fallback until column dropped |

Compose happens **only** in `core/persona.py` on the hub — never in the SPA or harness workers.

## Harness behaviour

| Harness | Apply point |
|---------|-------------|
| **claude-cli** | Subprocess spawn `--system-prompt-file`; respawn if prompt changes |
| **omp-rpc** | Session acquire / `set_system_prompt` at cold session (V2 parity) |

Both receive opaque `system_prompt: str` in `JobEnvelope.payload` — never `persona_json` or raw blob bytes.

## Limits

| Gate | Limit |
|------|-------|
| `soul.md` raw document | 48 KiB (`MAX_SOUL_DOCUMENT_BYTES`) |
| Composed prompt | 64 KiB (`MAX_PROMPT_BYTES`) |

Save rejected over limit — trim sections or split expertise/guidelines.

## Dashboard chat defaults

New chat tabs load `backend` + `model` from agent DB row (not hardcoded `claude-cli` / `sonnet`). Per-tab overrides in `localStorage` still apply; indicator shows when override ≠ DB default.

## OMP vs clipool checklist

1. Edit soul for agent with `backend=omp-rpc`
2. Open **new** dashboard chat (or `/reset` on TG/DC)
3. Confirm OMP session receives same composed string as clipool agent with identical soul (grep hub logs / job envelope `system_prompt` hash if needed)

## Memory (V1)

`soul_meta_json.memory.enabled` is provision-only — no `set_memory()` or vault identity anchor in this release. Memory line hidden in dashboard UI.

## Security / threat model (#1992)

Soul editor is **control-plane** access: anyone who can reach the dashboard BFF can mutate agent identity for all bots bound to that agent. V1 assumes **Tailnet-only** reachability (no public ingress). Dashboard BFF has no per-operator OIDC yet (#1992) — restrict Tailnet membership, audit `soul.put` logs, run secret lint before save. Do not embed API keys in soul markdown.

---

## Migration

Migrate agent souls from inline `persona_json` (SQLite) to AgentSoul v1 (`soul.md` in blobstore + DB pointers). The backfill has run in prod (commit `9805d4364`); this procedure is idempotent and stays useful for fresh installs or re-runs.

**Prerequisites:** `factory-hub` running with blobstore token wired (`factory_blobstore_token`, `init_blobstore()`). Snapshot `config.db` and blobstore **before** any migration step.

**Step A — Backup pair**

```bash
mkdir -p /tmp/factory-migration-$(date +%Y%m%d)
sqlite3 ~/.roxabi/factory/config.db ".backup '/tmp/factory-migration-$(date +%Y%m%d)/config.db'"
# Blobstore: see blobstore-backup-restore.md (index first, then shards)
```

**Step B — Schema migration (automatic on hub boot)** — hub applies SQLite migrations adding `soul_meta_json` (envelope only: display_name, memory provision), `soul_document_blob_ref` (`sha256:<hex>`), `soul_document_bytes` (metadata). `persona_json` remains for rollback until staging validates backfill.

**Step C — Backfill**

```bash
cd ~/projects/roxabi-factory   # or worktree path
uv run python scripts/backfill_soul_documents.py --dry-run   # inspect
uv run python scripts/backfill_soul_documents.py               # apply
```

Idempotent: agents already having `soul_document_blob_ref` are skipped. Logs report migrated / skipped / errors.

**Step D — Verify SQL**

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

**Step E — Hub smoke**

```bash
factory agent show <name>    # soul ref + meta visible
journalctl --user -u factory-hub -n 50 | grep -i soul || true
```

New conversations should receive composed `system_prompt` from blob cache. Active sessions keep prior soul until `/reset` or new pool (see § Soul is session-scoped).

---

## Rollback

Revert the soul blobstore migration while keeping the hub operational. The loader **falls back** to `persona_json` when the soul cache misses or the ref is NULL — rollback can be DB-only if `persona_json` was preserved (default during migration).

**When to use:** backfill produced wrong section mapping · blob ref points to corrupt `soul.md` · need immediate return to `persona_json` inline compose path.

**Step A — Stop mutating writes** — pause dashboard soul edits and `factory agent patch` on soul fields until rollback completes.

**Step B — Restore config.db (full rollback)**

```bash
systemctl --user stop factory-hub || true
cp /path/to/backup/config.db ~/.roxabi/factory/config.db
systemctl --user start factory-hub
```

Verify agents load and conversations work with inline persona.

**Step C — Partial rollback (single agent)** — clear blob pointer, keep `persona_json`:

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

**Step D — Blobstore orphans** — orphan blobs (no `blob_refs` row or superseded sha) are dedup-safe. Soul refs use `source=soul` and are **exempt** from the 30-day sweep — do not delete active refs manually without clearing the DB pointer first.

**Step E — Re-migrate** — after fixing root cause, re-run `scripts/backfill_soul_documents.py` and pair with the § Migration verification queries.
