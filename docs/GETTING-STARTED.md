# Getting Started — Lyra by Roxabi

Complete guide to set up Machine 1 (Ubuntu Server 26.04 LTS) as the Lyra hub from scratch.

---

## What you need

- Machine 1 (the hub) with Windows already installed
- Machine 2 (your daily driver) to SSH from
- USB key ≥ 8GB
- ~1 hour

---

## Choose your install path

Three ways to run lyra — pick the one that matches your goal.

| Tier | Goal | Setup |
|------|------|-------|
| **1. Library** | Import `lyra` in your own code | `uv add "lyra @ git+https://github.com/Roxabi/lyra.git@staging"` — nothing else |
| **2. Standalone** | Run lyra on one machine (dev or personal use) | See **Tier 2** below — 5 commands, no containers, no separate NATS server |
| **3. Full production** | 24/7 hub with adapters, auto-deploy, monitoring | Continue to **Step 1** below — this guide covers Machine 1 hub setup |

---

## Tier 2 — Standalone (unified mode)

For single-machine dev or personal use. `lyra start` runs hub + adapters in one process and auto-starts an embedded nats-server when `NATS_URL` is unset.

```bash
git clone git@github.com:Roxabi/lyra.git ~/projects/lyra
cd ~/projects/lyra && uv sync
cp config.toml.example config.toml   # edit owner_users with your IDs
lyra agent init                      # seed agents DB from bundled TOML
lyra bot add --platform telegram --bot-id lyra   # store token encrypted
lyra start                           # hub + telegram + discord in one process
```

No containers. No systemd. No `make deploy`. Stop with `Ctrl+C`.

Move to Tier 3 (split processes, auto-deploy timer, health monitoring, embedded NATS replaced by a system service) only when you actually need 24/7 uptime. Tier 3 is what this guide covers from **Step 1** onward.

---

## Step 1 — Create bootable USB (on Machine 2)

Download the ISO and flash it with Rufus (Windows) or dd (Linux):

**Rufus (recommended):**
```powershell
# Download Rufus portable
$r = Invoke-RestMethod 'https://api.github.com/repos/pbatard/rufus/releases/latest'
$url = ($r.assets | Where-Object { $_.name -match 'rufus-.*p\.exe$' }).browser_download_url
Invoke-WebRequest -Uri $url -OutFile "$env:TEMP\rufus.exe"
Start-Process "$env:TEMP\rufus.exe"
```

In Rufus: select the USB → load ISO → **GPT** + **UEFI (non CSM)** → Start.

**Or dd (WSL/Linux):**
```bash
wget -P /mnt/f/ https://releases.ubuntu.com/24.04.2/ubuntu-24.04.2-live-server-amd64.iso
# Then use Rufus to flash — dd to Windows-mounted USB is unreliable
```

---

## Step 2 — Free up disk space on Machine 1 (Windows)

If Machine 1 only has Windows, you need to shrink the C: partition to make room for Ubuntu.

Open **PowerShell as admin** on Machine 1:

```powershell
# Disable hibernation (frees space, allows deeper shrink)
powercfg /h off

# Disable pagefile (reboot required after)
$cs = Get-WmiObject Win32_ComputerSystem
$cs.AutomaticManagedPagefile = $false; $cs.Put()
(Get-WmiObject Win32_PageFileSetting).Delete()
Restart-Computer
```

After reboot, check max shrinkable space:
```powershell
"select disk 0
select partition 3
shrink querymax" | diskpart
```

> **Note:** If Windows blocks shrink (typical cap ~130GB despite free space), use `ntfsresize`
> from the Ubuntu installer shell instead — see Step 3 note.

---

## Step 3 — Install Ubuntu Server (physical, ~20 min)

1. Plug USB into Machine 1 → boot → press **F11** (boot menu) or **F2/Del** (BIOS)
2. Select **Ubuntu Server** (not minimized)
3. Follow the wizard:

**Storage configuration → Custom layout:**

| Partition | Size | Format | Mount |
|-----------|------|--------|-------|
| existing EFI | — | leave as-is | `/boot/efi` |
| new partition | 400GB+ | ext4 | `/` |
| new partition | ~30GB | swap | — |

> **Can't resize from the UI?** The installer doesn't support NTFS resize graphically.
> Open the installer shell (**Ctrl+Alt+F2**), then:
> ```bash
> ntfsresize -n -s 530G /dev/nvme0n1p3   # dry run
> ntfsresize -s 530G /dev/nvme0n1p3      # resize filesystem
> parted /dev/nvme0n1 resizepart 3 570GB # resize partition
> parted /dev/nvme0n1 mkpart primary ext4 570GB 970GB
> parted /dev/nvme0n1 mkpart primary linux-swap 970GB 999GB
> ```
> Then **Ctrl+Alt+F1** to return to the installer.

**SSH Setup step:** ✅ **Install OpenSSH server** → import key from GitHub: `gh:YourGitHubUsername`

4. Finish → reboot → remove USB

---

## Step 4 — First SSH connection (from Machine 2)

```bash
ssh yourname@<MACHINE_1_IP>
```

> Find the IP on Machine 1's boot screen or in your router's DHCP table.
> Tip: set a static DHCP reservation on your router (MAC binding) for a stable IP.

If you didn't import your key during install:
```bash
ssh-keygen -t ed25519 -C "machine2@lyra"
ssh-copy-id yourname@<MACHINE_1_IP>
```

---

## Step 5 — Run the provisioning script

```bash
ssh yourname@<MACHINE_1_IP>
curl -fsSL https://raw.githubusercontent.com/Roxabi/lyra/staging/deploy/provision.sh | ADMIN_USER=yourname bash
```

The script handles:
- System update + base packages (git, curl, htop, ffmpeg, build-essential, python3-dev…)
- NVIDIA drivers
- SSH hardening (key-only, no root login)
- UFW firewall (SSH only)
- fail2ban
- GRUB default Linux + Windows detection
- `lyra` agent account (restricted shell, no sudo)
- GitHub SSH host key in `known_hosts`
- uv (Python package manager)
- Podman (rootless container runtime — ships natively on Ubuntu 26.04 LTS)
- Node.js + Claude Code CLI
- agent-browser (headless browser for Claude Code)
- Git global config (interactive prompt)

If NVIDIA drivers were installed, reboot and reconnect:
```bash
sudo reboot
```

---

## Step 6 — Verify

```bash
ssh yourname@<MACHINE_1_IP> "
  lsb_release -d
  nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
  free -h | grep -E 'Mem|Swap'
  df -h /
  systemctl is-active ssh fail2ban
  uv --version
  podman --version
  claude --version
  ssh -T git@github.com 2>&1 | head -1
"
```

---

## Step 7 — Clone lyra and run setup

```bash
ssh yourname@<MACHINE_1_IP>

# Add your GitHub SSH key if not already done
# https://github.com/settings/keys → paste output of: cat ~/.ssh/id_ed25519.pub

git clone git@github.com:Roxabi/lyra.git ~/projects/lyra
cd ~/projects/lyra && python3 deploy/setup.py
```

`deploy/setup.py` will:
1. Check prerequisites (git, uv, podman, claude, GitHub SSH)
2. Clone and install **lyra** (core — always installed)
3. Prompt for optional modules:
   - **voiceCLI** — TTS/STT (requires NVIDIA GPU, ~3GB)
   - **roxabi-forge** — HTML diagram gallery
   - **imageCLI** — image generation CLI
   - **roxabi-vault** — knowledge vault
4. `make quadlet-install` — install Quadlet units to `~/.config/containers/systemd/`
5. Create log directories (`~/.local/state/*/logs/`)
6. Scaffold `config.toml` from example
7. Seed agents into the DB (`lyra agent init`)
8. Install Claude Code plugins:
   - **Mandatory:** `web-intel`, `agent-browser`, `lyra-send`, `refine-agent`
   - **Conditional:** `voice-cli` (auto-installed if voiceCLI was installed)
   - **Optional (prompted):** `dev-core`, `visual-explainer`, `compress`
9. Enable linger + start Quadlet containers

To install all optional modules and plugins without prompts:
```bash
python3 deploy/setup.py --all
```

---

## Step 8 — Configure

The setup scaffolded `config.toml` from the example. Fill in:

### 1. Auth config (`config.toml`)

```bash
cd ~/projects/lyra
nano config.toml
```

Fill in your user IDs in the `owner_users` arrays:
- Telegram ID: message [@userinfobot](https://t.me/userinfobot) on Telegram
- Discord ID: Settings → Advanced → Developer Mode → right-click your username → Copy User ID

### 2. Create your bots

**Telegram:**
1. Message [@BotFather](https://t.me/BotFather) on Telegram → `/newbot`
2. Note the token and username

**Discord:**
1. Go to [Discord Developer Portal](https://discord.com/developers/applications) → New Application
2. Bot tab → Reset Token → note the token
3. Enable **Message Content Intent** under Privileged Gateway Intents
4. OAuth2 → URL Generator → scopes: `bot` → permissions: `Send Messages`, `Read Message History`
5. Use the generated URL to invite the bot to your server

### 3. Store bot tokens in the credential store

Bot tokens are **not** stored in `.env`. They go into the encrypted credential store:

```bash
lyra bot add --platform telegram --bot-id lyra
# Prompts for: token, bot_username, webhook_secret

lyra bot add --platform discord --bot-id lyra
# Prompts for: token
```

This encrypts and stores the tokens in `~/.lyra/config.db`.

> **Note:** Bot tokens are encrypted in `~/.lyra/config.db` — not in `.env`. The `.env` file is for:
> - `DEPLOY_HOST`, `DEPLOY_DIR` — remote deployment target
> - `TELEGRAM_TOKEN`, `TELEGRAM_ADMIN_CHAT_ID` — monitoring timer (sends alerts directly)
> - `LYRA_HEALTH_SECRET` — bearer token for health endpoint
> - Voice settings (`LYRA_STT_ENABLED`, `LYRA_TTS_ENGINE`, etc.)
>
> See `.env.example` for all available options.

---

## Step 9 — Authenticate Claude CLI

```bash
claude
```

Follow the prompts to authenticate. Lyra uses Claude Code as its LLM backend — it spawns `claude --input-format stream-json` as a subprocess.

---

## Step 10 — NATS setup (production only)

For **single-machine development**, no NATS setup is required. `lyra start` auto-starts an embedded nats-server when `NATS_URL` is not set.

For **production** (Quadlet containers on `roxabi.network`):

```bash
cd ~/projects/lyra

# Generate nkeys and auth.conf for all identities
make nats-setup

# Create Podman secrets from the nkey files
make quadlet-secrets-install
```

`make nats-setup` generates:
- `~/.lyra/nkeys/*.seed` — nkey seed files for each identity (hub, telegram-adapter, discord-adapter, clipool-worker)
- `~/.lyra/nkeys/auth.conf` — merged auth config for NATS

The Quadlet NATS container (`lyra-nats.container`) runs rootless on `roxabi.network` with no TLS (container-to-container traffic is internal). Clients connect via `NATS_URL=nats://lyra-nats:4222`.

---

## Step 11 — Enable auto-start on boot

Quadlet units start automatically once installed and linger is enabled.

```bash
# Enable linger (allows systemd --user to run without a login session)
loginctl enable-linger $USER

# Install Quadlet units (already done by setup.py; repeat after updates)
make quadlet-install

# Create Podman secrets for nkey auth
make quadlet-secrets-install

# Start all Lyra containers now
systemctl --user start lyra-nats.service
sleep 3  # wait for NATS to be ready
systemctl --user start lyra-hub.service lyra-telegram.service lyra-discord.service lyra-clipool.service
```

Or use the Makefile dispatcher:
```bash
make lyra start
```

## Step 12 — Enable health monitoring

The monitoring system runs as a **systemd user timer** (separate from the Quadlet containers). It runs every 5 minutes, checks hub health, and sends Telegram alerts on anomalies.

```bash
cd ~/projects/lyra

# Ensure monitoring secrets are in .env
# TELEGRAM_TOKEN=<bot token for sending alerts>
# TELEGRAM_ADMIN_CHAT_ID=<your numeric Telegram user ID>

# Create the health secret file (used by /health/detail endpoint)
mkdir -p ~/.lyra/secrets
echo -n "$(openssl rand -hex 32)" > ~/.lyra/secrets/health_secret
chmod 600 ~/.lyra/secrets/health_secret

# Copy the same secret to .env so the monitoring cron can use it
echo "LYRA_HEALTH_SECRET=$(cat ~/.lyra/secrets/health_secret)" >> .env

# Install + enable the timer (done automatically by make register)
make monitor enable

# Verify it works
make monitor run      # trigger a manual check
make monitor status   # check result
```

## Step 13 — Verify services

```bash
cd ~/projects/lyra
systemctl --user status 'lyra-*.service' lyra-nats.service
```

You should see all five units active:
```
lyra-nats.service       active (running)
lyra-hub.service        active (running)
lyra-telegram.service   active (running)
lyra-discord.service    active (running)
lyra-clipool.service    active (running)
```

Or check the full container list:
```bash
podman ps --format "table {{.Names}}\t{{.Status}}"
```

Check the monitoring timer:
```bash
make monitor status
```

Check the logs:
```bash
make lyra logs        # journalctl for lyra-hub
make lyra errors      # journalctl for lyra-hub (errors only)
make monitor logs     # tail monitoring cron output (journalctl)
```

---

## Step 14 — Send your first message

**Telegram:** Open a DM with your bot and type anything. Lyra will respond.

**Discord:** @mention your bot in a channel: `@YourBot hello!`

What happens under the hood:
1. The adapter (standalone process) normalizes your message into an `InboundMessage`
2. It publishes to NATS (`lyra.inbound.<platform>.<bot_id>`)
3. The Hub picks it up via its `NatsBus` subscription and resolves the routing
4. The Hub publishes the turn to NATS (`lyra.clipool.cmd`); the `lyra-clipool` worker receives it and spawns the `claude` subprocess, streaming replies back via NATS (`lyra.clipool.heartbeat`)
5. The Hub publishes the response to NATS (`lyra.outbound.<platform>.<bot_id>`)
6. The `NatsOutboundListener` in the adapter process receives it and dispatches to the platform

---

## Step 15 — Set up lyra agent account (optional)

Generate a dedicated SSH key for the agent on Machine 2:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/lyra_agent -C "lyra-agent@machine2" -N ""
```

Add the public key to Machine 1:
```bash
ssh yourname@<MACHINE_1_IP> \
  "sudo bash -c 'echo \"$(cat ~/.ssh/lyra_agent.pub)\" >> /home/lyra/.ssh/authorized_keys && chmod 600 /home/lyra/.ssh/authorized_keys && chown lyra:lyra /home/lyra/.ssh/authorized_keys'"
```

Test:
```bash
ssh -i ~/.ssh/lyra_agent lyra@<MACHINE_1_IP> "id && git --version"
```

---

## Final state

| What | Where |
|------|-------|
| Admin access | `ssh yourname@<IP>` |
| Agent access | `ssh -i ~/.ssh/lyra_agent lyra@<IP>` (optional) |
| Lyra project | `~/projects/lyra/` |
| VoiceCLI project | `~/projects/voiceCLI/` (if installed) |
| Quadlet units | `~/.config/containers/systemd/lyra-*.container` |
| VoiceCLI Quadlet units | `~/.config/containers/systemd/voicecli-*.container` (if voiceCLI installed) |
| Config | `~/projects/lyra/config.toml` |
| Credentials | `~/.lyra/config.db` (encrypted, via `lyra bot add`) |
| Nkey seeds | `~/.lyra/nkeys/*.seed` |
| Podman secrets | `podman secret ls` (lyra-nats-auth, lyra-nkey-*) |
| Logs | `journalctl --user -u lyra-hub` |
| Diagrams | `~/.roxabi/forge/` (if installed) |
| Firewall | UFW, SSH only |

**Daily commands** (from `~/projects/lyra`):
```bash
make lyra status     # status of all lyra containers
make lyra reload     # restart hub + adapters + clipool
make lyra logs       # journalctl for lyra-hub
make deploy          # pull latest staging, install quadlet units (from Machine 2)
```

---

## Local demo without tokens

You can test the hub routing without any platform tokens:

```bash
cd ~/projects/lyra
uv run python demo.py
```

Expected output:
```
  -> Hello Lyra!
  <- Echo: Hello Lyra!
  -> How does routing work?
  <- Echo: How does routing work?
  -> Goodbye
  <- Echo: Goodbye

Done — 3 messages routed successfully.
```

This validates the full message path: bus → rate limiter → binding resolver → pool → agent → adapter dispatch.
