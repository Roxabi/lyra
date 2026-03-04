# Deployment — Machine 1 (Production)

Running Lyra as a managed system service on Machine 1 (Ubuntu Server 24.04).

## Overview

Lyra runs as a single Python process under the `lyra` system account, managed by systemd. The `lyra` user is created by `setup.sh` — it uses `rbash`, has no `sudo`, and its home directory is isolated from the admin account.

```
Machine 1 (roxabituwer, 192.168.1.16)
├── systemd unit: lyra.service
├── runs as: lyra (restricted bash, no sudo)
├── working directory: /home/lyra/lyra/
├── env file: /home/lyra/.env (mode 600)
└── logs: journald (journalctl -u lyra)
```

## Prerequisites

Machine 1 must be set up with `setup.sh` first. See [GETTING-STARTED.md](GETTING-STARTED.md).

```bash
# Verify the lyra user exists
id lyra

# Verify uv is available for the lyra user (install if not)
sudo -u lyra which uv || curl -LsSf https://astral.sh/uv/install.sh | sudo -u lyra sh
```

## 1. Deploy the code

```bash
# As admin on Machine 1
sudo -u lyra git clone https://github.com/roxabi/lyra /home/lyra/lyra
cd /home/lyra/lyra
sudo -u lyra uv sync --no-dev
```

For updates:

```bash
cd /home/lyra/lyra
sudo -u lyra git pull
sudo -u lyra uv sync --no-dev
sudo systemctl restart lyra
```

## 2. Configure environment

Create `/home/lyra/.env` as the `lyra` user:

```bash
sudo -u lyra tee /home/lyra/.env > /dev/null << 'EOF'
TELEGRAM_TOKEN=your-telegram-bot-token
TELEGRAM_WEBHOOK_SECRET=your-webhook-secret
TELEGRAM_BOT_USERNAME=your_bot_username
DISCORD_TOKEN=your-discord-bot-token
EOF

sudo chmod 600 /home/lyra/.env
sudo chown lyra:lyra /home/lyra/.env
```

Environment variables loaded at startup via `python-dotenv` (`load_dotenv()` in `__main__.py`). The `.env` file must be in the working directory or the user home — systemd `WorkingDirectory` points to the repo root, which is where `load_dotenv()` looks first.

## 3. Install the systemd service

Create `/etc/systemd/system/lyra.service`:

```ini
[Unit]
Description=Lyra AI agent engine
Documentation=https://github.com/roxabi/lyra
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=lyra
Group=lyra
WorkingDirectory=/home/lyra/lyra
EnvironmentFile=/home/lyra/.env
ExecStart=/home/lyra/.local/bin/uv run python -m lyra
Restart=on-failure
RestartSec=10
TimeoutStopSec=30

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=lyra

# Hardening
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=full

[Install]
WantedBy=multi-user.target
```

```bash
# Enable and start
sudo systemctl daemon-reload
sudo systemctl enable lyra
sudo systemctl start lyra

# Verify
sudo systemctl status lyra
```

## 4. Manage the service

```bash
# Status
sudo systemctl status lyra

# Start / stop / restart
sudo systemctl start lyra
sudo systemctl stop lyra
sudo systemctl restart lyra

# View logs (live)
journalctl -u lyra -f

# View last 100 lines
journalctl -u lyra -n 100

# Logs since boot
journalctl -u lyra -b
```

## 5. Enable debug logging

Lyra uses Python's `logging` module. The `basicConfig` in `__main__.py` defaults to `INFO`. To enable debug output without modifying code, override the log level via environment variable:

```bash
# Add to /home/lyra/.env
LOG_LEVEL=DEBUG
```

Then update `__main__.py` to read it:

```python
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
```

Until that change is made, edit `basicConfig(level=logging.DEBUG)` directly.

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

## Troubleshooting

**Service fails to start — "Missing required env var"**
The `.env` file is either missing, has wrong permissions, or is not in the working directory. Check:
```bash
sudo -u lyra cat /home/lyra/lyra/.env   # should print vars
sudo systemctl status lyra              # check the ExecStart path
```

**Service restarts in a loop**
`Restart=on-failure` will retry on crash. Check logs for the root cause:
```bash
journalctl -u lyra -n 50
```

**`uv` not found for lyra user**
```bash
sudo -u lyra bash -c 'source ~/.bashrc && which uv'
# If not found:
curl -LsSf https://astral.sh/uv/install.sh | sudo -u lyra sh
```

**NVIDIA GPU not visible**
```bash
nvidia-smi     # if this fails, drivers need reinstalling
# See GETTING-STARTED.md for NVIDIA driver setup
```
