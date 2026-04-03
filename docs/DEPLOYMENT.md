# Deployment — Machine 1 (Production)

Running Lyra as a managed service on Machine 1 (Ubuntu Server 24.04).

## Overview

Lyra runs as **three separate processes** managed by **supervisord**. All daemons (lyra_hub, lyra_telegram, lyra_discord, voicecli_tts, voicecli_stt) are managed by a single supervisord instance. A **systemd user unit** (`lyra.service`) with linger ensures everything auto-starts on boot — no login session required.

```
Machine 1 (roxabituwer, 192.168.1.16)
├── systemd: nats.service         (NATS server — independent, always-on)
├── systemd user unit: lyra.service (auto-start, linger enabled)
│   └── supervisord (lyra)
│       ├── lyra_hub      ← hub process (NatsBus, pool, LLM, memory)
│       ├── lyra_telegram ← Telegram adapter (thin NATS client)
│       └── lyra_discord  ← Discord adapter (thin NATS client)
├── program configs: ~/projects/lyra/deploy/supervisor/conf.d/
│   ├── lyra_hub.conf
│   ├── lyra_telegram.conf
│   └── lyra_discord.conf
├── working directory: ~/projects/lyra/
├── env file: ~/projects/lyra/.env
└── logs: ~/.local/state/lyra/logs/ (rotating, 10 MB × 3-5 files per program)
```

## Prerequisites

Machine 1 must be set up with the provision script. See [GETTING-STARTED.md](GETTING-STARTED.md).

## 1. Deploy the code

```bash
# From Machine 2 — pull, test, restart on Machine 1
make deploy
```

This runs `scripts/deploy.sh` on Machine 1. The script checks **two repos independently**:

| Repo | Branch | Services restarted on change |
|------|--------|------------------------------|
| `lyra` | `origin/staging` | `lyra_hub`, `lyra_telegram`, `lyra_discord` |
| `voiceCLI` | `origin/staging` | `voicecli_tts`, `voicecli_stt` |

**Smart restart** — only the services whose repo changed are restarted. If neither repo has new commits, the script exits without touching supervisor.

**Auto re-lock** — when voiceCLI updates, the script also runs `uv lock --upgrade-package voicecli` inside Lyra's `.venv` so the pinned dependency stays in sync, then marks Lyra as updated too (both sets of adapters restart).

**Test gate** — after pulling `lyra`, `pytest` runs before the restart. A test failure rolls back to the previous commit; voiceCLI is not pulled in that run.

**Graceful drain** — on restart, the running process finishes any in-flight Claude CLI turns (up to 60 s) before exiting. Conversations that complete within the window are transparent to users; only turns that outlast 60 s receive a "please resend" notification. Supervisor's `stopwaitsecs=75` gives the drain window a 15 s buffer before force-kill.

**Deploy log** — every run is appended to `~/.local/state/lyra/logs/deploy.log`.

For a manual update on Machine 1:

```bash
cd ~/projects/lyra
git pull origin staging
uv sync --all-extras --frozen
make lyra reload
```

## 2. Configure environment

Create `~/projects/lyra/.env` on Machine 1:

```bash
# Telegram (required if using Telegram adapter)
TELEGRAM_TOKEN=your-telegram-bot-token
TELEGRAM_WEBHOOK_SECRET=any-random-string
TELEGRAM_BOT_USERNAME=your_bot_username

# Discord (required if using Discord adapter)
DISCORD_TOKEN=your-discord-bot-token

# NATS (required — hub + adapters communicate over NATS)
NATS_URL=nats://127.0.0.1:4222

# Optional
ANTHROPIC_API_KEY=sk-ant-...     # for anthropic-sdk backend
LYRA_HEALTH_SECRET=...           # for authenticated /health endpoint
LYRA_CONFIG_SECRET=...           # for /config HTTP endpoint
```

```bash
chmod 600 ~/projects/lyra/.env
```

## Multi-Bot Deployment

Multiple bots are configured in `config.toml` — no supervisor changes needed. The three-process topology (`lyra_hub`, `lyra_telegram`, `lyra_discord`) is fixed regardless of how many bots are configured.

### Environment variables

Add one set of variables per additional bot. The variable names are arbitrary; reference them in `config.toml` with the `env:` prefix.

```bash
# Second bot — Telegram
ARYL_TELEGRAM_TOKEN=123456789:ABCdef...

# Second bot — Discord
ARYL_DISCORD_TOKEN=MTIz...

# Webhook secret per bot (if using Telegram webhook mode)
ARYL_TELEGRAM_WEBHOOK_SECRET=another-random-string
```

```bash
chmod 600 ~/projects/lyra/.env
```

The `.env` file grows by two to three lines per additional bot. No other infrastructure changes are needed.

### Resource considerations

All bots share the `lyra_hub` process and a single `CliPool` (Claude CLI subprocess pool). Adapter processes (`lyra_telegram`, `lyra_discord`) are lightweight thin NATS clients. Implications:

- **CPU / RAM**: each additional bot adds a small constant overhead (auth middleware, one Pool per conversation scope). At personal-use scale this is negligible — expect under 50 MB additional RAM per bot, all in `lyra_hub`.
- **CliPool contention**: simultaneous long-running LLM requests from multiple bots compete for subprocess slots in the shared pool in `lyra_hub`. `CliPool` has no pool-size configuration — the only mitigations are reducing load or running a separate Hub process per bot.
- **Crash scope**: an unhandled exception in `lyra_hub` takes down all bot routing at once. The adapter processes survive independently. Supervisor's `autorestart=true` brings everything back automatically.

### Supervisor: no changes needed

The three supervisor programs (`lyra_hub`, `lyra_telegram`, `lyra_discord`) remain fixed — adding bots only changes `config.toml`. Changes take effect on restart.

```bash
# Restart all three Lyra processes after updating config.toml or .env
make lyra reload
```

Verify all bots started cleanly:

```bash
make lyra logs      # tail lyra_hub stdout
# Look for lines like:
# INFO lyra.bootstrap.hub_standalone: Registered Telegram bot bot_id='lyra'
# INFO lyra.bootstrap.hub_standalone: Registered Telegram bot bot_id='aryl'
# INFO lyra.adapters.discord: Discord bot ready: RoxabiLyra (id=<id>)
# INFO lyra.adapters.discord: Discord bot ready: RoxabiAryl (id=<id>)
```

---

## 3. Register with supervisord

```bash
# One-time setup on Machine 1
cd ~/projects/lyra
make register    # installs supervisor program configs and systemd unit
```

## 4. Manage the service

All commands can be run from Machine 1 or from Machine 2 via SSH (`make remote <cmd>`).

```bash
# From Machine 1
cd ~/projects/lyra
make lyra          # status
make lyra reload   # restart
make lyra stop     # stop
make lyra logs     # tail stdout
make lyra errors   # tail stderr

# From Machine 2 (via SSH)
make remote status
make remote reload
make remote logs
make remote errors
```

Or use supervisorctl directly on Machine 1:

```bash
cd ~/projects/lyra
make ps            # all programs
make lyra          # lyra status
make lyra reload   # restart lyra
make lyra logs     # tail stdout
```

## 5. Enable debug logging

Lyra writes rotating logs to `~/.local/state/lyra/logs/`. The log level defaults to `INFO`. To enable debug output, set `LOG_LEVEL` in `.env` and add support in `_setup_logging()`:

```bash
# ~/.lyra/.env
LOG_LEVEL=DEBUG
```

Until the env var is wired, edit `basicConfig(level=logging.DEBUG)` in `__main__.py` directly.

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

`setup.sh` sets UFW to deny all inbound except SSH. If you add webhook mode for Telegram, open the webhook port:

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

The `lyra.service` systemd user unit manages supervisord lifecycle on boot.

```bash
# Check unit status
systemctl --user status lyra

# Enable auto-start (already done on provisioned machines)
systemctl --user enable lyra.service
loginctl enable-linger $USER

# Restart all services via systemd
systemctl --user restart lyra

# View systemd journal
journalctl --user -eu lyra.service --no-pager -n 50
```

> **Note:** `start.sh` and `supervisorctl.sh` (in `deploy/supervisor/`) use full paths to
> `$HOME/.local/bin/supervisord` and `$HOME/.local/bin/supervisorctl`
> because systemd does not include `~/.local/bin` on PATH.

---

## Troubleshooting

**Service fails to start — "Missing required env var"**
The `.env` file is either missing, has wrong permissions, or is not in the working directory. Check:
```bash
cat ~/projects/lyra/.env   # should print vars
make lyra errors           # check the startup log
```

**Service restarts in a loop**
supervisord will retry on crash (`autorestart=true`). Check stderr logs for the root cause:
```bash
make lyra errors
```

**`uv` not found**
```bash
which uv
# If not found:
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**NVIDIA GPU not visible**
```bash
nvidia-smi     # if this fails, drivers need reinstalling
# See GETTING-STARTED.md for NVIDIA driver setup
```
