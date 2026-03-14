# Getting Started — Lyra by Roxabi

Complete guide to set up Machine 1 (Ubuntu Server 24.04 LTS) as the Lyra hub from scratch.

---

## What you need

- Machine 1 (the hub) with Windows already installed
- Machine 2 (your daily driver) to SSH from
- USB key ≥ 8GB
- ~1 hour

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

## Step 5 — Run the setup script

```bash
ssh yourname@<MACHINE_1_IP>
curl -fsSL https://raw.githubusercontent.com/Roxabi/lyra/main/setup.sh | ADMIN_USER=yourname bash
```

Or with an explicit username override:
```bash
ADMIN_USER=yourname bash setup.sh
```

The script handles:
- System update
- Base packages (git, curl, htop, nvtop…)
- NVIDIA drivers
- SSH hardening (key-only, no root login)
- UFW firewall (SSH only)
- fail2ban
- GRUB default Linux + Windows detection
- `lyra` agent account (restricted shell, no sudo)

If NVIDIA drivers were installed, reboot:
```bash
sudo reboot
```

---

## Step 6 — Verify

```bash
# GPU
ssh yourname@<MACHINE_1_IP> "nvidia-smi"

# Swap
ssh yourname@<MACHINE_1_IP> "free -h"
# If swap shows 0: sudo mkswap /dev/nvme0n1p6 && sudo swapon /dev/nvme0n1p6
# Then persist: echo '/dev/nvme0n1p6 none swap sw 0 0' | sudo tee -a /etc/fstab

# Full checkup
ssh yourname@<MACHINE_1_IP> "
  lsb_release -d
  nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader
  free -h | grep -E 'Mem|Swap'
  df -h /
  systemctl is-active ssh fail2ban
"
```

---

## Step 7 — Set up lyra agent account

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

Expected: `uid=1001(lyra) ... git version 2.x`

---

## Final state

| What | Where |
|------|-------|
| Admin access | `ssh yourname@<IP>` |
| Agent access | `ssh -i ~/.ssh/lyra_agent lyra@<IP>` |
| GPU | `nvidia-smi` ✓ |
| GRUB | Linux default, Windows on F11 |
| Firewall | UFW, SSH only |
| SSH keys | `~/.ssh/id_ed25519` (admin), `~/.ssh/lyra_agent` (agent) |

Machine 1 is ready to run Lyra by Roxabi.

---

## Step 8 — Clone the repo & install dependencies

```bash
ssh yourname@<MACHINE_1_IP>

# Clone (use SSH if you imported your GitHub key)
git clone git@github.com:Roxabi/lyra.git ~/lyra
cd ~/lyra

# Install uv (Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.bashrc

# Install all dependencies (runtime + dev)
uv sync --all-extras
```

Verify:
```bash
uv run python -c "from lyra.core.hub import Hub; print('Hub OK')"
```

---

## Step 9 — Configure environment variables

Copy the example and fill in your tokens:

```bash
cp .env.example .env
nano .env
```

Required variables for running Lyra with real adapters:

```bash
# Telegram — get from @BotFather on Telegram
TELEGRAM_TOKEN=123456:ABC-DEF...
TELEGRAM_BOT_USERNAME=your_bot_username
TELEGRAM_WEBHOOK_SECRET=a-random-secret-string

# Discord — get from https://discord.com/developers/applications
DISCORD_TOKEN=your-discord-bot-token
```

**Telegram setup:**
1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. `/newbot` → choose a name and username
3. Copy the token → `TELEGRAM_TOKEN`
4. The username (without @) → `TELEGRAM_BOT_USERNAME`
5. Generate a random webhook secret → `TELEGRAM_WEBHOOK_SECRET`

**Discord setup:**
1. Go to [Discord Developer Portal](https://discord.com/developers/applications) → New Application
2. Bot tab → Reset Token → copy → `DISCORD_TOKEN`
3. Enable **Message Content Intent** under Privileged Gateway Intents
4. OAuth2 → URL Generator → scopes: `bot` → permissions: `Send Messages`, `Read Message History`
5. Use the generated URL to invite the bot to your server

---

## Step 9b — Create config.toml (auth config)

`config.toml` is required to start Lyra with networked adapters. It is gitignored — never committed.

```bash
cp config.toml.example config.toml
nano config.toml
```

Fill in your user IDs. Lyra supports two config schemas — single-bot (simple) and multi-bot:

**Single-bot (default)** — one bot per platform, tokens from env vars:

```toml
[admin]
# owner_users are automatically admin — only add extra IDs here if needed.
user_ids = []

[auth.telegram]
default = "blocked"
owner_users = [YOUR_TELEGRAM_ID]   # numeric — get from @userinfobot on Telegram
trusted_users = []                 # numeric Telegram IDs (can interact, cannot admin)

[auth.discord]
default = "blocked"
owner_users = [YOUR_DISCORD_ID]    # numeric — Settings → Advanced → Developer Mode → right-click username
trusted_roles = []                 # numeric Discord role snowflake IDs (trusted access)
```

**Multi-bot** — multiple bots per platform, each mapped to a different agent:

```toml
[[telegram.bots]]
bot_id = "lyra"
token = "env:TELEGRAM_TOKEN"
bot_username = "env:TELEGRAM_BOT_USERNAME"
webhook_secret = "env:TELEGRAM_WEBHOOK_SECRET"
agent = "lyra_default"

[[discord.bots]]
bot_id = "lyra"
token = "env:DISCORD_TOKEN"
auto_thread = true
agent = "lyra_default"

[[auth.telegram_bots]]
bot_id = "lyra"
default = "blocked"
owner_users = [YOUR_TELEGRAM_ID]

[[auth.discord_bots]]
bot_id = "lyra"
default = "blocked"
owner_users = [YOUR_DISCORD_ID]
```

See `config.toml.example` for a full multi-bot example with multiple bots.

> **Note:** `owner_users` in each adapter section are automatically granted admin privileges. You do not need to duplicate them in `[admin].user_ids`.

> **See also:** For a full guide on running and customizing multiple bots, see [MULTI-BOT.md](MULTI-BOT.md).

At least one bot (Telegram or Discord) must be configured. A missing platform logs a warning and skips that adapter — Lyra still starts with the remaining ones.

---

## Step 10 — Run the tests

```bash
cd ~/lyra
uv run pytest -v
```

All tests should pass. They use mocks — no tokens or network required.

---

## Step 11 — Start Lyra

```bash
cd ~/lyra
uv run python -m lyra
```

You should see:
```
INFO lyra.__main__: Agent loaded: name=lyra_default model=claude-haiku-4-5-20251001 backend=claude-cli
INFO lyra.__main__: Lyra started — adapters: telegram, discord, health on :8443.
INFO lyra.adapters.discord: Discord bot ready: YourBot#1234 (id=...)
```

Stop with **Ctrl+C** (graceful shutdown).

> **Note:** The `claude` CLI must be installed and authenticated on the machine.
> Lyra spawns `claude --input-format stream-json` as a subprocess — it uses your
> Claude Code subscription, not an API key.

---

## Step 12 — Send your first message

**Telegram:** Open a DM with your bot and type anything. Lyra will respond.

**Discord:** @mention your bot in a channel: `@YourBot hello!`

What happens under the hood:
1. The adapter normalizes your message into an `InboundMessage` object
2. It goes onto the hub's async bus (`asyncio.Queue`)
3. The hub resolves the routing (wildcard binding → your user gets an isolated pool)
4. `SimpleAgent` sends the text to a persistent `claude` subprocess
5. The response is dispatched back to the originating platform

---

## Step 13 — Local demo without tokens

You can test the hub routing without any platform tokens. Save this as `demo.py` at the project root:

```python
"""Minimal demo: hub + echo agent + fake adapter — no tokens needed."""

import asyncio
from datetime import datetime, timezone

from lyra.core.agent import Agent, AgentBase
from lyra.core.hub import Hub
from lyra.core.message import InboundMessage, OutboundMessage, Platform, Response
from lyra.core.pool import Pool


class EchoAgent(AgentBase):
    """Echoes back whatever the user sends."""

    async def process(self, msg: InboundMessage, pool: Pool) -> Response:
        return Response(content=f"Echo: {msg.text}")


class FakeAdapter:
    """Prints responses to stdout instead of sending to a platform."""

    def __init__(self) -> None:
        self.responses: list[OutboundMessage] = []

    async def send(self, original_msg: InboundMessage, outbound: OutboundMessage) -> None:
        self.responses.append(outbound)
        print(f"  <- {outbound.to_text()}")


async def main() -> None:
    hub = Hub()

    # Wire up
    agent = EchoAgent(Agent(name="echo", system_prompt="", memory_namespace="test"))
    hub.register_agent(agent)

    adapter = FakeAdapter()
    hub.register_adapter(Platform.TELEGRAM, "main", adapter)
    hub.register_binding(Platform.TELEGRAM, "main", "*", "echo", "telegram:main:*")

    # Start per-platform inbound queues, then hub consumer
    await hub.inbound_bus.start()
    hub_task = asyncio.create_task(hub.run())

    # Simulate messages
    for text in ["Hello Lyra!", "How does routing work?", "Goodbye"]:
        msg = InboundMessage(
            id=f"demo-{text[:5]}",
            platform="telegram",
            bot_id="main",
            scope_id="chat:123",
            user_id="tg:user:42",
            user_name="Mickael",
            is_mention=True,
            text=text,
            text_raw=text,
            timestamp=datetime.now(timezone.utc),
            platform_meta={"chat_id": 123},
        )
        print(f"  -> {text}")
        hub.inbound_bus.put(Platform.TELEGRAM, msg)

    # Let the hub process all messages
    await hub.bus.join()

    print(f"\nDone — {len(adapter.responses)} messages routed successfully.")
    hub_task.cancel()
    await hub.inbound_bus.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

Run it:
```bash
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

This validates the full message path: bus -> rate limiter -> binding resolver -> pool -> agent -> adapter dispatch.
