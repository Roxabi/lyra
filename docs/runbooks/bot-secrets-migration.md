# Runbook — Migrating pre-#1057 `bot_secrets` rows (one-shot)

**When:** one-time only, on M₁, when a pre-existing `~/.roxabi/factory/config.db`
carries a legacy `bot_secrets` table (pre-#1057 encrypted-DB credential layer).
Operators on machines that never seeded that table can skip this entirely — bot
tokens are Podman secrets now (see `docs/CONFIGURATION.md` § Bot credentials).

## Migrate

The one-shot migration script reads each `bot_secrets` row, decrypts via the
existing Fernet keyring, and provisions a Podman secret per `(platform, bot_id)`.
The script is self-contained (it does NOT depend on the deleted `CredentialStore`
class) and idempotent:

```bash
python3 tools/migrate_bot_secrets_to_podman.py            # apply
python3 tools/migrate_bot_secrets_to_podman.py --dry-run  # preview
```

## After migration

1. Re-render the Quadlet with the newly-provisioned secrets: `make quadlet-install`.
2. Restart the adapters so they remount the new secrets.
3. Optionally drop the now-orphan `bot_secrets` table:

```bash
sqlite3 ~/.roxabi/factory/config.db 'DROP TABLE bot_secrets'
```

The script prints the same post-migration checklist on success.
