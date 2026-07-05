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

**Enforcement status: not yet wired.** Today this is operator discipline, not a machine-checked control:

- `rotation_log_append()` is only called from the disaster-recovery path ([`secrets_reset.py`](../../src/factory/cli/secrets_reset.py)) — routine per-identity rotation via [nkey-rotation.md](nkey-rotation.md) Path A/B does not yet log, so the rotation log is not a complete age source for every identity.
- There is no `make check-seed-age` (or equivalent) target — nothing currently reads the log and fails a stale identity.

Wiring the routine path and adding an age-check target is tracked in [#2246](https://github.com/Roxabi/roxabi-factory/issues/2246); until it ships, treat the 90-day max as a calendar reminder to act on, not a gate that will catch a missed rotation.

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
