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

## Rotate one nkey

```bash
make nats-regen-authconf
podman secret create --replace factory-nats-hub ~/.roxabi/factory/nkeys/hub.seed
systemctl --user restart factory-nats
systemctl --user restart factory-hub
```

## Rotate all nkey secrets

```bash
./deploy/install.sh --force --secrets-only
systemctl --user restart factory-nats factory-hub factory-telegram factory-discord factory-clipool
```

## Rotate GitHub App PEM

```bash
podman secret create --replace factory-gh-pem ~/.roxabi/factory/gh-app.pem
systemctl --user restart factory-gh-helper
```

## Rotate BlobStore bearer token

`type=mount` — restart mandatory after `podman secret create --replace`.

```bash
printf '%s' "$NEW_TOK" > ~/.roxabi/factory/blobstore.tok && chmod 0600 ~/.roxabi/factory/blobstore.tok
podman secret create --replace factory_blobstore_token ~/.roxabi/factory/blobstore.tok
systemctl --user restart factory-blobstore.service
podman exec factory-blobstore sh -c 'head -c 8 /run/secrets/factory_blobstore_token'
```

Rollback: restore `.prev` source file, `--replace` secret, restart service.

Full backup procedure → [blobstore-backup-restore.md](blobstore-backup-restore.md).