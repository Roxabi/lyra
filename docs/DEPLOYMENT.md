# Deployment — Machine 1 (Production)

Running Lyra as a managed service on Machine 1 (Ubuntu Server 26.04 LTS) using Podman Quadlet units. For the full Quadlet reference, see [DEPLOYMENT-quadlet.md](DEPLOYMENT-quadlet.md).

> **Legacy note:** Before #611, Lyra ran under supervisord. That stack has been removed from
> the repo (#886). Supervisord is no longer the production path.

## Overview

Lyra runs as **five containers** on a shared `roxabi.network` bridge, managed by **Podman Quadlet** (systemd --user). A `linger`-enabled systemd user session ensures all containers auto-start on boot without a login session.

```
Machine 1 (roxabituwer, 192.168.1.16)
├── systemd --user (linger enabled)
│   ├── lyra-nats.service         ← Quadlet NATS container (port 4222, roxabi.network)
│   ├── lyra-hub.service          ← hub container (NatsBus, pool, routing, memory)
│   ├── lyra-telegram.service     ← Telegram adapter container
│   ├── lyra-discord.service      ← Discord adapter container
│   └── lyra-clipool.service      ← CliPool NATS worker (Claude subprocesses)
├── Quadlet unit files: ~/.config/containers/systemd/
│   ├── roxabi.network
│   ├── lyra-hub.container
│   ├── lyra-telegram.container
│   ├── lyra-discord.container
│   ├── lyra-clipool.container
│   ├── lyra-nats.container
│   └── lyra-*.volume
├── config: ~/projects/lyra/config.toml
├── credentials: ~/.lyra/config.db (encrypted, via `lyra bot add`)
├── nkey seeds: ~/.lyra/nkeys/*.seed
├── Podman secrets: lyra-nats-auth, lyra-nkey-*
└── logs: journalctl --user -u lyra-hub
```

## Prerequisites

Machine 1 must be set up with the provision script. See [GETTING-STARTED.md](GETTING-STARTED.md).

Machine 1 requires:
- Ubuntu 26.04 LTS (ships Podman 5.x natively via apt)
- Linger enabled: `loginctl enable-linger $USER`
- Image built on Machine 2 and pushed: `make build && make push`

## 1. Deploy the code

```bash
# From Machine 2 — build image, push to Machine 1, install Quadlet units, restart
make build && make push
```

On Machine 1:

```bash
cd ~/projects/lyra
make quadlet-install   # copy Quadlet units to ~/.config/containers/systemd/
make lyra reload       # restart containers via systemctl --user
```

**Test gate** — after pulling `lyra`, `pytest` runs before the restart. A test failure aborts.

**Graceful drain** — on restart, the running container finishes any in-flight Claude CLI turns
(up to 60 s) before stopping. Conversations that complete within the window are transparent to
users; only turns that outlast 60 s receive a "please resend" notification.

**Deploy log** — every run is appended to `~/.local/state/lyra/logs/deploy.log`.

For a manual image rebuild on Machine 1:

```bash
cd ~/projects/lyra
git pull origin staging
make build
make quadlet-install
make lyra reload
```

## 2. Configure environment

**Credentials (tokens):** Bot tokens are stored encrypted in `~/.lyra/config.db` via `lyra bot add`. Adapters read directly from this database at startup — no env vars needed for tokens.

```bash
# Store bot tokens (run once per bot)
lyra bot add --platform telegram --bot-id lyra
lyra bot add --platform discord --bot-id lyra
```

**Environment inline in `.container` files:** NATS connection vars are set directly in each Quadlet unit:

```ini
# In lyra-hub.container, lyra-telegram.container, etc.
Environment=NATS_URL=nats://lyra-nats:4222
Environment=NATS_NKEY_SEED_PATH=/run/secrets/hub.seed
```

**Nkey secrets:** Seed files are mounted as Podman secrets from `~/.lyra/nkeys/`:

```bash
# Generate nkeys + auth.conf
make nats-setup

# Create Podman secrets
make quadlet-secrets-install
```

See [DEPLOYMENT-quadlet.md](DEPLOYMENT-quadlet.md) for the full volume and secret layout.

## Multi-Bot Deployment

Multiple bots are configured in `config.toml` — no container changes needed. The five-container
topology (`lyra-nats`, `lyra-hub`, `lyra-telegram`, `lyra-discord`, `lyra-clipool`) is fixed regardless of how many bots
are configured.

### Adding a second bot

1. Add the bot entry in `config.toml`:
```toml
[[telegram.bots]]
bot_id = "aryl"

[[auth.telegram_bots]]
bot_id = "aryl"
default = "blocked"
owner_users = []
```

2. Store the bot token:
```bash
lyra bot add --platform telegram --bot-id aryl
```

3. Restart containers:
```bash
make lyra reload
```

### Resource considerations

All bots share the `lyra-hub` container for routing and the `lyra-clipool` container for Claude subprocess execution.
Adapter containers (`lyra-telegram`, `lyra-discord`) are lightweight thin NATS clients.

- **CPU / RAM**: each additional bot adds a small constant overhead. At personal-use scale this
  is negligible — expect under 50 MB additional RAM per bot across hub and clipool.
- **CliPool contention**: simultaneous long-running LLM requests from multiple bots compete for
  subprocess slots in the shared `lyra-clipool` container.
- **Crash scope**: an unhandled exception in `lyra-hub` takes down all bot routing at once. The
  adapter containers survive independently. systemd `Restart=on-failure` brings everything back.

---

## 3. Install Quadlet units

```bash
# One-time setup on Machine 1 — installs units and reloads systemd
cd ~/projects/lyra
make quadlet-install
```

This copies all `.container`, `.volume`, and `.network` files from `deploy/quadlet/` to
`~/.config/containers/systemd/` and runs `systemctl --user daemon-reload`.

## 4. Manage the service

All commands can be run from Machine 1 or from Machine 2 via SSH (`make remote <cmd>`).

```bash
# From Machine 1
cd ~/projects/lyra
make lyra          # status
make lyra reload   # restart
make lyra stop     # stop
make lyra logs     # tail lyra-hub stdout
make lyra errors   # tail lyra-hub stderr

# From Machine 2 (via SSH)
make remote status
make remote reload
make remote logs
make remote errors
```

## 5. Enable debug logging

Lyra logs go to journald. The log level defaults to `INFO`. To enable debug output, set
`LOG_LEVEL=DEBUG` in `~/.lyra/env/hub.env` and restart:

```bash
make lyra reload
journalctl --user -u lyra-hub -f
```

## 6. Monitor VRAM (Machine 1)

```bash
# Live VRAM usage
watch -n 2 nvidia-smi

# One-shot summary
nvidia-smi --query-gpu=memory.used,memory.free,memory.total --format=csv
```

Expected at rest (Phase 1 — TTS + embeddings not yet running):
- **VRAM used**: ~0 GB (hub + adapters are CPU-only)
- **RAM used**: ~200-400 MB

Expected under load (Phase 2 — with TTS and embeddings):
- **VRAM**: ~5.5 GB (TTS ~5 GB + embeddings ~0.5 GB) out of 10 GB

## 7. Firewall (UFW)

`provision.sh` sets UFW to deny all inbound except SSH. If you add webhook mode for Telegram,
open the webhook port:

```bash
# Open port 8443 for Telegram webhooks (if switching to webhook mode)
sudo ufw allow 8443/tcp comment "Telegram webhook"
sudo ufw status
```

Polling mode (the default) requires no inbound ports beyond SSH.

## 8. Remote control from Machine 2 (Makefile)

Machine connection is read from `.env`:

```bash
# .env (on your dev machine)
DEPLOY_HOST=user@your-hub-ip          # SSH user@host for production hub
DEPLOY_DIR=~/projects/lyra            # project path on the production host
```

### Deploy (pull + test + restart)

```bash
make deploy
```

### Remote service control

```bash
make remote stop      # stop Lyra
make remote status    # check status
make remote reload    # restart Lyra
make remote logs      # tail stdout logs
make remote errors    # tail stderr logs
```

---

## 9. systemd auto-start

Quadlet units are started by systemd --user with linger enabled. No separate wrapper unit is
needed.

```bash
# Enable linger (run once — survives reboots)
loginctl enable-linger $USER

# Check all Lyra unit statuses
systemctl --user status 'lyra-*.service' nats.service

# View journald logs
journalctl --user -u lyra-hub --no-pager -n 50
journalctl --user -u lyra-telegram --no-pager -n 50
```

---

## 10. NATS ACL Rollout

When the subject→identity ACL matrix changes (spec #706), regenerate nkeys and update the Podman secret.

### Regenerate

```bash
cd ~/projects/lyra
./deploy/nats/gen-nkeys.sh --regenerate --yes
```

This rotates all nkeys — old seeds are backed up to `~/.lyra/nkeys.bak.{epoch}/` and the old
`auth.conf` is backed up before any files are overwritten.

### Update Podman secret and reload

```bash
make quadlet-secrets-install   # recreate Podman secrets from new seeds
systemctl --user restart lyra-nats.service
```

### Reconnect clients

Restart adapters and clipool so they reconnect with new credentials:

```bash
make telegram reload && make discord reload
make clipool reload
make lyra-hub reload  # hub last
```

> Voice workers (TTS/STT) live in the voiceCLI project and are reloaded via its own Makefile
> targets.

### Verify

```bash
scripts/check-nats-acls.sh --since "$(date -Iseconds)" --window 90
```

---

## Troubleshooting

**Container fails to start — "Missing required env var"**
The env file is either missing, has wrong permissions, or references an unset variable. Check:
```bash
journalctl --user -u lyra-hub --no-pager -n 50
make lyra errors
```

**Container restarts in a loop**
systemd `Restart=on-failure` retries on crash. Check the journal for the root cause:
```bash
journalctl --user -u lyra-hub -f
```

**`uv` not found (inside container)**
The image bundles `uv` — if it is missing, the image was built incorrectly. Rebuild:
```bash
make build && make push
```

**NVIDIA GPU not visible**
```bash
nvidia-smi     # if this fails, drivers need reinstalling
# See GETTING-STARTED.md for NVIDIA driver setup
```
