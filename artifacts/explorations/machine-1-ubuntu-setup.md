# Machine 1 — Ubuntu Server 24.04 LTS Setup (Dual Boot)

> Issue: #6 (closed)
> Status: Done
> Last updated: 2026-03-02

---

## Overview

| Context | Steps |
|---------|-------|
| 🖥 Physical (20-30 min) | USB key → BIOS → Installer → 1st reboot |
| 💻 SSH from Machine 2 | GRUB → NVIDIA drivers → reboot → checks |

**SSH pivot**: during the installer, check "Install OpenSSH server". From the 1st reboot onwards, everything is done from Machine 2.

---

## Context

Machine 1 = Lyra by Roxabi central hub.

| Spec | Value |
|------|-------|
| CPU | AMD Ryzen 7 5800X |
| RAM | 32GB |
| GPU | RTX 3080 10GB VRAM |
| Target OS | Ubuntu Server 24.04 LTS (dual boot Windows, default Linux) |

---

## Prerequisites

- USB key ≥ 8GB
- Ubuntu Server 24.04 LTS ISO: https://ubuntu.com/download/server
- Bootable USB tool: Rufus (Windows) or `dd` (Linux)
- Machine 2 available to verify SSH

---

## 🖥 PHYSICAL — Step 1: Prepare bootable USB

Can be done on Machine 2 before going to Machine 1:

```bash
# On Machine 2 (Linux)
dd if=ubuntu-24.04-live-server-amd64.iso of=/dev/sdX bs=4M status=progress && sync
```

Or via Rufus on Windows (GPT + UEFI, not MBR).

---

## 🖥 PHYSICAL — Step 2: Install Ubuntu (dual boot)

**Warning**: Windows must already be installed and working.

1. Plug USB key into Machine 1
2. Boot and press F2/Del → boot from USB key
3. At partitioning, choose **"Use free space"** or **custom**:

| Partition | Size | Type | Mount point |
|-----------|------|------|-------------|
| EFI (existing Windows) | — | EFI | `/boot/efi` |
| Ubuntu root | 400GB | ext4 | `/` |
| Swap | 29GB | swap | — |

**Do not format** the Windows EFI partition — just select it as `/boot/efi`.

> Note: If Windows fills the entire disk, you need to shrink the NTFS partition first.
> The Ubuntu Server installer cannot resize NTFS via the UI — use `ntfsresize` from the installer shell:
> ```bash
> # In installer shell (Ctrl+Alt+F2)
> ntfsresize -n -s 530G /dev/nvme0n1p3   # dry run
> ntfsresize -s 530G /dev/nvme0n1p3      # actual resize
> parted /dev/nvme0n1 resizepart 3 570GB
> parted /dev/nvme0n1 mkpart primary ext4 570GB 970GB
> parted /dev/nvme0n1 mkpart primary linux-swap 970GB 999GB
> ```

4. **"SSH Setup"** step → ✅ **Install OpenSSH server**
   - Option: import key from GitHub (`gh:<username>`) → passwordless SSH from 1st boot

5. Finish installation → reboot → remove USB key

**From here, everything is done from Machine 2 via SSH.**

---

## 💻 SSH — Step 3: Initial connection from Machine 2

```bash
# Find Machine 1 IP (if unknown: check console at boot, or router)
ssh mickael@<IP_MACHINE_1>
```

If SSH key was not imported during install:

```bash
# On Machine 2
ssh-keygen -t ed25519 -C "machine2@lyra"
ssh-copy-id mickael@<IP_MACHINE_1>   # asks for password one last time
```

---

## 💻 SSH — Step 4: GRUB config (default Linux)

```bash
sudo nano /etc/default/grub
```

```ini
GRUB_DEFAULT=0                    # 0 = first entry = Ubuntu
GRUB_TIMEOUT=5                    # 5s to choose
GRUB_TIMEOUT_STYLE=menu
GRUB_DISABLE_OS_PROBER=false      # detect Windows
```

```bash
sudo update-grub
# Verify Windows Boot Manager appears in the list
```

---

## 💻 SSH — Step 5: NVIDIA Drivers (RTX 3080)

```bash
# Check GPU detected
lspci | grep -i nvidia

# Install recommended drivers
sudo apt update
sudo apt install -y nvidia-driver-550

# Mandatory reboot
sudo reboot
```

After reboot:

```bash
nvidia-smi
```

---

## 💻 SSH — Step 6: Final checks

```bash
# GPU
nvidia-smi

# SSH accessible from Machine 2
ssh mickael@<IP_MACHINE_1> "echo OK"

# GRUB default Linux
grep GRUB_DEFAULT /etc/default/grub

# Services at startup
sudo systemctl is-enabled ssh
```

---

## Acceptance criteria

- [x] `ssh mickael@192.168.1.16` works from Machine 2
- [x] `nvidia-smi` shows RTX 3080 (10240 MiB VRAM)
- [x] Machine boots Linux by default (GRUB_DEFAULT=0)
- [x] Windows still accessible via GRUB

---

## Troubleshooting

### GRUB does not detect Windows

```bash
sudo os-prober
sudo update-grub
```

If os-prober is disabled:

```bash
echo 'GRUB_DISABLE_OS_PROBER=false' | sudo tee -a /etc/default/grub
sudo update-grub
```

### NVIDIA driver — module not loaded

```bash
sudo modprobe nvidia
dmesg | grep -i nvidia
# If Secure Boot is enabled: enroll MOK during reboot (choose "Enroll MOK" at blue screen)
```

### SSH — connection refused

```bash
# Check firewall
sudo ufw status
sudo ufw allow ssh

# Check service
sudo systemctl restart ssh
```

---

## Post-install notes

- IP fixed via DHCP reservation on router (MAC binding) for stable SSH address
- Pagefile disabled on Windows before shrinking NTFS — can be re-enabled after
- MOK enrolled at first reboot after NVIDIA driver install (Secure Boot)
