# Deployment — Machine 1 (Production)

Running Lyra as a managed service on Machine 1 (Ubuntu Server 24.04).

## Overview

Lyra runs as a single Python process managed by **supervisord** via `lyra-stack`. All daemons (lyra, voicecli_tts, voicecli_stt) are managed by a single supervisord instance.

```
Machine 1 (roxabituwer, 192.168.1.16)
├── supervisord (lyra-stack)
├── lyra program config: ~/projects/lyra/supervisor/lyra.conf
├── symlinked into: ~/projects/lyra-stack/conf.d/lyra.conf
├── working directory: ~/projects/lyra/
├── env file: ~/projects/lyra/.env
└── logs: ~/.local/state/lyra/logs/ (rotating, 10 MB × 5 files)
```

## Prerequisites

Machine 1 must be set up with `lyra-stack` and the provision script. See [GETTING-STARTED.md](GETTING-STARTED.md).

## 1. Deploy the code

```bash
# From Machine 2 — pull, test, restart on Machine 1
make deploy
```

This runs `scripts/deploy.sh` on Machine 1: fetches `main`, runs tests (rolls back on failure), restarts Lyra via supervisord.

For a manual update on Machine 1:

```bash
cd ~/projects/lyra
git pull
uv sync --no-dev
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

# Optional
ANTHROPIC_API_KEY=sk-ant-...     # for anthropic-sdk backend
LYRA_HEALTH_SECRET=...           # for authenticated /health endpoint
LYRA_CONFIG_SECRET=...           # for /config HTTP endpoint
```

```bash
chmod 600 ~/projects/lyra/.env
```

## Multi-Bot Deployment

Running multiple bots requires no changes to the supervisor configuration — all bots run in the single `lyra` process.

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

All bots share a single process and a single `CliPool` (Claude CLI subprocess pool). Implications:

- **CPU / RAM**: each additional bot adds a small constant overhead (one adapter, one auth middleware instance). At personal-use scale this is negligible — expect under 50 MB additional RAM per bot.
- **CliPool contention**: simultaneous long-running LLM requests from multiple bots compete for subprocess slots in the shared pool. `CliPool` has no pool-size configuration — subprocess contention cannot be tuned from config. The only mitigations are reducing load or running a separate Lyra process per bot.
- **Crash scope**: an unhandled exception that kills the process takes down all bots at once. The supervisor's `autorestart=true` policy brings everything back automatically.

### Supervisor: no changes needed

The `lyra` supervisor program starts `lyra start`, which reads `config.toml` and starts all configured bots. Adding bots to `config.toml` takes effect on the next restart.

```bash
# Restart after updating config.toml or .env
make lyra reload
```

Verify all bots started cleanly:

```bash
make lyra logs
# Look for lines like:
# INFO lyra.__main__: Registered Telegram bot bot_id='lyra' agent='lyra_default'
# INFO lyra.__main__: Registered Telegram bot bot_id='aryl' agent='aryl_default'
# INFO lyra.adapters.discord: Discord bot ready: RoxabiLyra (id=<id>)
# INFO lyra.adapters.discord: Discord bot ready: RoxabiAryl (id=<id>)
```

---

## 3. Register with supervisord

```bash
# One-time setup on Machine 1
cd ~/projects/lyra
make register    # creates symlink in lyra-stack/conf.d/
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
cd ~/projects/lyra-stack
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

Machine connection is read from `.env` (with hardcoded fallbacks):

```bash
# .env (Machine 2)
MACHINE1_HOST=mickael@192.168.1.16
MACHINE1_DIR=~/projects/lyra
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
