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
