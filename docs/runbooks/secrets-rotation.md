# Runbook — Secret layout & rotation

> **Lost all nkey seeds?** Use [secrets-disaster-recovery.md](secrets-disaster-recovery.md) (`factory secrets reset`) — this runbook is for routine rotation of individual secrets.

## Secret layout

| Podman secret | Source file | Mounted at |
|---|---|---|
| `factory-nats-hub` | `~/.roxabi/factory/nkeys/hub.seed` | `/run/secrets/factory-nats-hub.seed` |
| `factory-nats-telegram` | `~/.roxabi/factory/nkeys/telegram-adapter.seed` | `/run/secrets/factory-nats-telegram.seed` |
| `factory-nats-discord` | `~/.roxabi/factory/nkeys/discord-adapter.seed` | `/run/secrets/factory-nats-discord.seed` |
| `factory-nats-clipool` | `~/.roxabi/factory/nkeys/clipool-worker.seed` | `/run/secrets/factory-nats-clipool.seed` |
| `factory-nats-omp` | `~/.roxabi/factory/nkeys/omp-worker.seed` | `/run/secrets/factory-nats-omp.seed` |
| `factory-gh-pem` | `~/.roxabi/factory/gh-app.pem` | `/run/secrets/gh-app.pem` |
| `factory-claude-oauth` | `~/.roxabi/factory/claude-oauth.tok` | `CLAUDE_CODE_OAUTH_TOKEN` env (clipool) |
| `factory-litellm-key` | `~/.roxabi/factory/litellm-key.tok` | `LITELLM_API_KEY` env (omp) |
| `factory_blobstore_token` | `~/.roxabi/factory/blobstore.tok` | `/run/secrets/factory_blobstore_token` |

NATS `auth.conf` is an **inline bind mount**, not a Podman secret (ADR-085). Nkey seeds use `type=mount` (tmpfs). OAuth/LiteLLM use `type=env`.

## Rotation policy & schedule

Governs *when* a secret must rotate. The *how* is the per-secret procedures above and in the cross-referenced runbooks below.

**Max seed age: 90 days.** Every identity in [`acl-matrix.json`](../../deploy/nats/acl-matrix.json) must not exceed 90 days since its last rotation (per the P2 recommendation in [nats-acl-inbox-case-postmortem.md](../history/nats-acl-inbox-case-postmortem.md)).

**Event-triggered rotation — rotate immediately, regardless of the quarterly cadence:**

- Suspected compromise (seed exfiltrated, exposed in a backup, etc.) — procedure: [nkey-rotation.md](nkey-rotation.md).
- Personnel change with prod (M₁) access — an operator loses (or no longer needs) prod access.
- A seed observed somewhere it shouldn't be — logs, a build artifact, a screen share.

**Scheduled rotation.** Absent an event trigger, rotate every identity on a quarterly calendar reminder (~90 days). Rotating an identity resets its own clock only — it does not reset the age of any other identity.

**Rotation log.** Every rotation — scheduled or event-triggered — gets one entry in `~/.roxabi/factory/rotation-log.md` (created on first write; see [operator-log.md](operator-log.md) for the full audit-log map). Entry format, written by `rotation_log_append()` in [`deploy/lib/operator-log.sh`](../../deploy/lib/operator-log.sh):

```
YYYY-MM-DD | secret:<identity> | reason:<reason> | by:<operator> | trigger:<manual|scheduled|compromise|...> | host:<hostname>
```

If a rotation runs outside an instrumented script (raw `podman secret create`, manual seed swap), append the entry by hand — see "Manual operations" in [operator-log.md](operator-log.md).

**Enforcement (nkey seeds, #2246).** For identities in [`acl-matrix.json`](../../deploy/nats/acl-matrix.json), the 90-day max is machine-checked on M₁:

- **Logging.** Routine rotation via [nkey-rotation.md](nkey-rotation.md) Path A (`make nats-add-identity`) and Path B (`factory-acl genkeys`) calls `rotation_log_append()` automatically. Disaster recovery ([`secrets_reset.py`](../../src/factory/cli/secrets_reset.py)) and blobstore regen (`deploy/install.sh`) also append. Manual rotations outside those paths must still append by hand (see [operator-log.md](operator-log.md)).
- **Age check.** `make check-seed-age` reads `rotation-log.md` as the sole authoritative age source: **warn at ≥75 days**, **fail at ≥90 days**. Seed-file mtime is informational only (Syncthing/rsync/restore reset it). Identities with **no log entry** are in bootstrap grace — the check never gates them until their first logged rotation.
- **Schedule.** `factory-check-seed-age.timer` runs the check daily on M₁. It is **not** a CI/merge gate — a stale identity surfaces as `factory-check-seed-age.service` in `failed` state and `systemctl --user is-system-running` → `degraded`. Bootstrap grace, fail-open logging, and triage notes → [nkey-rotation.md § Bootstrap grace](nkey-rotation.md#bootstrap-grace--rotation-log-and-seed-age-policy).

**Non-nkey secrets** (GitHub App PEM, OAuth, LiteLLM, blobstore) follow the same event-triggered and quarterly cadence rules above but are **not** covered by `check-seed-age` — operator discipline plus the rotation log for instrumented paths.

## Rotate one nkey

```bash
make nats-regen-authconf
podman secret create --replace factory-nats-hub ~/.roxabi/factory/nkeys/hub.seed
systemctl --user restart factory-nats
systemctl --user restart factory-hub
```

## Rotate all nkey secrets

```bash
./deploy/install.sh --force-secrets --secrets-only
systemctl --user restart factory-nats factory-hub factory-telegram factory-discord factory-clipool
```

## Rotate GitHub App PEM

```bash
podman secret create --replace factory-gh-pem ~/.roxabi/factory/gh-app.pem
systemctl --user restart factory-gh-helper
```

## Rotate BlobStore bearer token

`type=mount` — restart mandatory after `podman secret create --replace`. Record the rotation in `~/.roxabi/factory/rotation-log.md` (or use the instrumented path below).

**Preferred (logged):**

```bash
./deploy/install.sh --force-regen-blobstore --secrets-only
systemctl --user restart factory-blobstore factory-hub factory-telegram factory-discord
systemctl --user restart voicecli-stt voicecli-tts
# voiceCLI bind-mounts ~/.roxabi/factory/blobstore.tok — no separate env copy on M₁
```

**Manual:**

```bash
printf '%s' "$NEW_TOK" > ~/.roxabi/factory/blobstore.tok && chmod 0600 ~/.roxabi/factory/blobstore.tok
podman secret create --replace factory_blobstore_token ~/.roxabi/factory/blobstore.tok
systemctl --user restart factory-blobstore.service
# append one line to ~/.roxabi/factory/rotation-log.md (see operator-log.md)
podman exec factory-blobstore sh -c 'head -c 8 /run/secrets/factory_blobstore_token'
```

Rollback: restore `.prev` source file, `--replace` secret, restart service.

Full backup procedure → [blobstore-backup-restore.md](blobstore-backup-restore.md).
