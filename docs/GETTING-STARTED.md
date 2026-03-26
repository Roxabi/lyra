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

## Step 5 — Run the provisioning script

```bash
ssh yourname@<MACHINE_1_IP>
curl -fsSL https://raw.githubusercontent.com/Roxabi/lyra-stack/main/scripts/provision.sh | ADMIN_USER=yourname bash
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
- supervisord (process manager)
- Node.js + Claude Code CLI
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
  supervisord --version
  claude --version
  ssh -T git@github.com 2>&1 | head -1
"
```

---

## Step 7 — Clone lyra-stack and run setup

This single command clones all repos, installs dependencies, registers services, scaffolds config, and starts everything:

```bash
ssh yourname@<MACHINE_1_IP>

# Add your GitHub SSH key if not already done
# https://github.com/settings/keys → paste output of: cat ~/.ssh/id_ed25519.pub

git clone git@github.com:Roxabi/lyra-stack.git ~/projects/lyra-stack
cd ~/projects/lyra-stack && make setup
```

`make setup` will:
1. Check prerequisites (git, uv, supervisord, claude, GitHub SSH)
2. Clone lyra and voiceCLI → `~/projects/`
3. `uv sync` in each project
4. `make register` in each (creates supervisor symlinks)
5. Create log directories (`~/.local/state/*/logs/`)
6. Symlink `voicecli` to `~/.local/bin/`
7. Scaffold `.env` and `config.toml` from examples
8. Seed agents into the DB (`lyra agent init`)
9. Start supervisord

For optional modules (imageCLI, roxabi-vault):
```bash
make setup ARGS=--all
```

---

## Step 8 — Configure

The setup scaffolded `.env` and `config.toml` from examples. Three things to fill in:

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

This encrypts and stores the tokens in `~/.lyra/auth.db`.

> **Note:** `.env` is for non-secret config only (TTS engine, STT model size, monitoring settings).
> See `.env.example` for all available options.

---

## Step 9 — Authenticate Claude CLI

```bash
claude
```

Follow the prompts to authenticate. Lyra uses Claude Code as its LLM backend — it spawns `claude --input-format stream-json` as a subprocess.

---

## Step 10 — Start Lyra

```bash
cd ~/projects/lyra-stack
make lyra reload
make ps
```

You should see:
```
lyra_telegram    RUNNING   pid 12345, uptime 0:00:10
lyra_discord     RUNNING   pid 12346, uptime 0:00:10
voicecli_tts     RUNNING   pid 12347, uptime 0:00:10
voicecli_stt     RUNNING   pid 12348, uptime 0:00:10
```

Check the logs:
```bash
make lyra logs      # tail Telegram adapter stdout
make lyra errlogs   # tail stderr (where INFO/ERROR logs go)
```

---

## Step 11 — Send your first message

**Telegram:** Open a DM with your bot and type anything. Lyra will respond.

**Discord:** @mention your bot in a channel: `@YourBot hello!`

What happens under the hood:
1. The adapter normalizes your message into an `InboundMessage` object
2. It goes onto the hub's async bus (`asyncio.Queue`)
3. The hub resolves the routing (wildcard binding → your user gets an isolated pool)
4. `SimpleAgent` sends the text to a persistent `claude` subprocess
5. The response is dispatched back to the originating platform

---

## Step 12 — Set up lyra agent account (optional)

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
| VoiceCLI project | `~/projects/voiceCLI/` |
| Supervisor hub | `~/projects/lyra-stack/` |
| Config | `~/projects/lyra/.env` + `config.toml` |
| Credentials | `~/.lyra/auth.db` (encrypted) |
| Logs | `~/.local/state/lyra/logs/` |
| GPU | `nvidia-smi` ✓ |
| Firewall | UFW, SSH only |

**Daily commands** (from `~/projects/lyra-stack`):
```bash
make ps              # status of all services
make lyra reload     # restart lyra
make lyra logs       # tail logs
make deploy          # pull latest + restart (from Machine 2)
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
