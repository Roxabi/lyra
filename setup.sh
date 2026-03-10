#!/usr/bin/env bash
# Lyra by Roxabi — Machine 1 post-install setup script
# Usage: curl -fsSL https://raw.githubusercontent.com/Roxabi/lyra/main/setup.sh | bash
#        curl -fsSL https://raw.githubusercontent.com/Roxabi/lyra/main/setup.sh | ADMIN_USER=yourname bash
#        curl -fsSL https://raw.githubusercontent.com/Roxabi/lyra/main/setup.sh | ADMIN_USER=yourname AGENT_USER=myagent bash
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"
source "$HOME/.local/bin/env" 2>/dev/null || true  # uv

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()    { echo -e "${GREEN}[+]${NC} $1"; }
warn()    { echo -e "${YELLOW}[!]${NC} $1"; }
error()   { echo -e "${RED}[x]${NC} $1"; exit 1; }
section() { echo -e "\n${GREEN}=== $1 ===${NC}"; }

# Admin user (defaults to current user, override with ADMIN_USER=yourname)
ADMIN_USER="${ADMIN_USER:-$(whoami)}"
# Agent user (defaults to lyra, override with AGENT_USER=anotherame)
AGENT_USER="${AGENT_USER:-lyra}"
info "Running setup for admin: $ADMIN_USER, agent: $AGENT_USER"

section "System update"
sudo apt update && sudo apt upgrade -y

section "Base packages"
sudo apt install -y \
  curl wget git htop nvtop \
  fail2ban ufw \
  build-essential \
  ffmpeg

section "moviepy (dedicated venv)"
MOVIEPY_VENV="$HOME/.venvs/moviepy"
if [ -x "$MOVIEPY_VENV/bin/python" ]; then
  info "moviepy venv already exists."
else
  python3 -m venv "$MOVIEPY_VENV"
  "$MOVIEPY_VENV/bin/pip" install moviepy
  info "moviepy installed in $MOVIEPY_VENV (use $MOVIEPY_VENV/bin/python to run scripts)."
fi

section "NVIDIA drivers"
if nvidia-smi &>/dev/null; then
  warn "NVIDIA drivers already installed, skipping."
else
  sudo apt install -y nvidia-driver-550
  warn "Reboot required after script finishes to activate NVIDIA drivers."
  NEEDS_REBOOT=true
fi

section "SSH hardening"
SSHD_CONF="/etc/ssh/sshd_config.d/lyra.conf"
if [ -f "$SSHD_CONF" ]; then
  info "SSH hardening already configured."
else
  sudo tee "$SSHD_CONF" > /dev/null << 'EOF'
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
EOF
  sudo systemctl restart ssh
  info "SSH: password auth disabled, key-only."
fi

section "Firewall (ufw)"
if sudo ufw status | grep -q "Status: active"; then
  info "UFW already active."
else
  sudo ufw default deny incoming
  sudo ufw default allow outgoing
  sudo ufw allow ssh
  sudo ufw --force enable
  info "UFW enabled: only SSH allowed inbound."
fi

section "fail2ban"
if systemctl is-active --quiet fail2ban; then
  info "fail2ban already active."
else
  sudo systemctl enable fail2ban
  sudo systemctl start fail2ban
  info "fail2ban active."
fi

section "GRUB — default Linux"
GRUB_CHANGED=false
if ! grep -q "GRUB_DISABLE_OS_PROBER=false" /etc/default/grub; then
  echo 'GRUB_DISABLE_OS_PROBER=false' | sudo tee -a /etc/default/grub > /dev/null
  GRUB_CHANGED=true
fi
if ! grep -q "^GRUB_DEFAULT=0" /etc/default/grub; then
  sudo sed -i 's/^GRUB_DEFAULT=.*/GRUB_DEFAULT=0/' /etc/default/grub
  GRUB_CHANGED=true
fi
if ! grep -q "^GRUB_TIMEOUT=5" /etc/default/grub; then
  sudo sed -i 's/^GRUB_TIMEOUT=.*/GRUB_TIMEOUT=5/' /etc/default/grub
  GRUB_CHANGED=true
fi
if [ "$GRUB_CHANGED" = true ]; then
  sudo update-grub
  info "GRUB updated: Linux default, Windows detectable."
else
  info "GRUB already configured."
fi

section "Agent account ($AGENT_USER)"
if id "$AGENT_USER" &>/dev/null; then
  warn "User '$AGENT_USER' already exists, skipping."
else
  sudo useradd -m -s /bin/bash -c "Lyra by Roxabi AI agent" "$AGENT_USER"
  sudo passwd -l "$AGENT_USER"
  sudo mkdir -p /home/"$AGENT_USER"/.ssh
  sudo chmod 700 /home/"$AGENT_USER"/.ssh
  sudo chown -R "$AGENT_USER":"$AGENT_USER" /home/"$AGENT_USER"/.ssh
  sudo chmod 750 /home/"$ADMIN_USER"
  info "User '$AGENT_USER' created (bash, no sudo, isolated home)."
  warn "Add your agent SSH public key to /home/$AGENT_USER/.ssh/authorized_keys"
fi

section "External tools (ADR-010: Install, Wrap, Declare)"
# System CLIs used by Lyra agents and roxabi-plugins skills.
# Each tool is installed on PATH; wrapped by a skill in roxabi-plugins;
# declared in agent TOML config. See docs/architecture/adr/010-*.mdx.

if command -v voicecli &>/dev/null; then
  info "voicecli already installed."
else
  if command -v uv &>/dev/null; then
    out=$(uv tool install git+https://github.com/roxabi/voiceCLI 2>&1) && info "voicecli installed." || warn "voicecli install failed: $out"
  else
    warn "uv not found, skipping voicecli install. Run: uv tool install voicecli"
  fi
fi

if command -v imagecli &>/dev/null; then
  info "imagecli already installed."
else
  if command -v uv &>/dev/null; then
    out=$(uv tool install git+https://github.com/roxabi/imageCLI 2>&1) && info "imagecli installed." || warn "imagecli install failed: $out"
  else
    warn "uv not found, skipping imagecli install. Run: uv tool install imagecli"
  fi
fi

# Google Workspace CLI — see issue #65.
# Install: https://github.com/googleworkspace/cli
if command -v gws &>/dev/null; then
  info "gws already installed."
else
  warn "gws not installed. See: https://github.com/googleworkspace/cli"
fi

section "Done"
info "Setup complete — admin: $ADMIN_USER, agent: $AGENT_USER"
if [ "${NEEDS_REBOOT:-false}" = true ]; then
  warn "NVIDIA drivers installed — reboot now: sudo reboot"
fi
