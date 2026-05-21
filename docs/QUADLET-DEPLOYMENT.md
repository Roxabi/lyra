# Quadlet Deployment — Lyra

Runbook for installing, operating, and rotating secrets in the Lyra Quadlet deployment on M₁ (`roxabituwer`).

→ SSoT standards: `~/projects/docs/container-deployment-standard.md`
→ Component manifest: `deploy/quadlet.toml`
→ Idempotent install script: `deploy/install.sh`

## Architecture

Six containers on `roxabi.network` (systemd `--user`, linger enabled):

| Service | Container | Role |
|---|---|---|
| `lyra-nats` | NATS 2.x | Message bus (port 4222) |
| `lyra-hub` | lyra | Hub — routing, pool, memory |
| `lyra-telegram` | lyra | Telegram adapter |
| `lyra-discord` | lyra | Discord adapter |
| `lyra-clipool` | lyra | CliPool NATS worker (Claude subprocesses) |
| `lyra-gh-helper` | lyra | GitHub App token-mint helper (`lyra-gh.pod`) |

## Install

### First-time setup

```bash
# 1. Generate nkeys + auth.conf (if not done yet)
cd ~/projects/lyra
make nats-setup

# 2. Run idempotent install
cd ~/projects/lyra
./deploy/install.sh

# 3. Start services
systemctl --user start lyra-nats lyra-hub lyra-telegram lyra-discord lyra-clipool lyra-gh-helper

# 4. Verify
systemctl --user status 'lyra-*'
podman ps
```

### Re-deploy after code change

Auto-update handles this for GHCR images. Manual:

```bash
cd ~/projects/lyra
make quadlet-install    # copy units + daemon-reload
# then restart affected services
systemctl --user restart lyra-hub lyra-telegram lyra-discord lyra-clipool
```

## Secret layout

| Podman secret | Source file | Mounted at |
|---|---|---|
| `lyra-nats-auth` | `~/.lyra/nkeys/auth.conf` | `/etc/nats/nkeys/auth.conf` (in lyra-nats) |
| `lyra-nats-hub` | `~/.lyra/nkeys/hub.seed` | `/run/secrets/lyra-nats-hub.seed` |
| `lyra-nats-telegram` | `~/.lyra/nkeys/telegram-adapter.seed` | `/run/secrets/lyra-nats-telegram.seed` |
| `lyra-nats-discord` | `~/.lyra/nkeys/discord-adapter.seed` | `/run/secrets/lyra-nats-discord.seed` |
| `lyra-nats-clipool` | `~/.lyra/nkeys/clipool-worker.seed` | `/run/secrets/lyra-nats-clipool.seed` |
| `lyra-gh-pem` | `~/.lyra/gh-app.pem` | `/run/secrets/gh-app.pem` (helper only) |
| `lyra-claude-oauth` | `~/.lyra/claude-oauth.tok` | `CLAUDE_CODE_OAUTH_TOKEN` env (clipool) |

All seed secrets use `type=mount` (tmpfs-backed). `lyra-claude-oauth` uses `type=env`. (S7, S18)

## Secret rotation

### Rotate an nkey seed

```bash
# 1. Generate new seed
cd ~/projects/lyra
make nats-regen-authconf    # re-renders auth.conf; auto-creates missing seeds

# 2. Re-install the affected secret
podman secret create --replace lyra-nats-hub ~/.lyra/nkeys/hub.seed

# 3. Reload NATS (auth.conf change), then restart the affected container
systemctl --user restart lyra-nats
systemctl --user restart lyra-hub
```

### Rotate all nkey secrets at once

```bash
cd ~/projects/lyra
./deploy/install.sh --force --secrets-only
systemctl --user restart lyra-nats lyra-hub lyra-telegram lyra-discord lyra-clipool
```

### Rotate GitHub App PEM

```bash
# Copy new PEM to ~/.lyra/gh-app.pem, then:
podman secret create --replace lyra-gh-pem ~/.lyra/gh-app.pem
systemctl --user restart lyra-gh-helper
```

## Diagnostic

```bash
# Service status
systemctl --user status 'lyra-*'

# Running containers
podman ps --filter 'name=lyra'

# Logs
journalctl --user -u lyra-hub -f
journalctl --user -u lyra-telegram -f
journalctl --user -u lyra-clipool -f
journalctl --user -u lyra-gh-helper -f

# NATS health
curl -s http://127.0.0.1:8222/varz | python3 -m json.tool | grep -E '"connections"|"version"'

# List Podman secrets
podman secret ls

# Verify secret is readable in a container
podman exec lyra-hub cat /run/secrets/lyra-nats-hub.seed | head -c 4

# Auto-update status
podman auto-update --dry-run
systemctl --user status podman-auto-update.timer
```

## Common issues

| Symptom | Likely cause | Fix |
|---|---|---|
| `lyra-hub` fails to start | Missing `lyra-nats-hub` secret | `./deploy/install.sh --secrets-only` |
| NATS auth failure in logs | Stale auth.conf | `make nats-regen-authconf` + restart NATS |
| `lyra-gh-helper` fails | Missing `lyra-gh-pem` | Install PEM secret (see above) |
| Container restart loop | `RestartSec=10` applies — check logs | `journalctl --user -u <svc> -n 50` |
| Auto-update not pulling | Timer inactive | `systemctl --user start podman-auto-update.timer` |

## References

- `deploy/quadlet/` — unit files (authoritative)
- `deploy/quadlet.toml` — component manifest
- `deploy/install.sh` — idempotent install script
- `docs/DEPLOYMENT.md` — full deployment guide
- `~/projects/docs/container-deployment-standard.md` — 18 standards SSoT
