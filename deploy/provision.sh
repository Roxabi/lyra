#!/usr/bin/env bash
# Lyra by Roxabi — Machine 1 post-install provisioning script
# Usage: curl -fsSL https://raw.githubusercontent.com/Roxabi/lyra/staging/deploy/provision.sh | bash
#        curl -fsSL https://raw.githubusercontent.com/Roxabi/lyra/staging/deploy/provision.sh | ADMIN_USER=yourname bash
#        curl -fsSL https://raw.githubusercontent.com/Roxabi/lyra/staging/deploy/provision.sh | ADMIN_USER=yourname AGENT_USER=myagent bash
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

# ── System packages ──────────────────────────────────────────────────────────

section "System update"
sudo apt update && sudo apt upgrade -y

section "Base packages"
sudo apt install -y \
  curl wget git htop nvtop \
  fail2ban ufw \
  build-essential python3-dev portaudio19-dev \
  ffmpeg wtype wl-clipboard \
  libgirepository-2.0-dev libcairo2-dev

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

# ── Security ─────────────────────────────────────────────────────────────────

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

# ── Boot ─────────────────────────────────────────────────────────────────────

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

# ── Users ────────────────────────────────────────────────────────────────────

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

# ── GitHub SSH ───────────────────────────────────────────────────────────────

section "GitHub SSH host key"
if ssh-keygen -F github.com &>/dev/null; then
  info "GitHub host key already in known_hosts."
else
  ssh-keyscan github.com >> "$HOME/.ssh/known_hosts" 2>/dev/null
  info "GitHub host key added to known_hosts."
fi

# Verify GitHub SSH authentication
if ssh -T git@github.com 2>&1 | grep -q "successfully authenticated"; then
  info "GitHub SSH authentication OK."
else
  warn "GitHub SSH not authenticated. Add your SSH key at https://github.com/settings/keys"
  warn "Your public key: $(cat "$HOME/.ssh/id_ed25519.pub" 2>/dev/null || echo 'no key found — run ssh-keygen first')"
fi

# ── Dev tools ────────────────────────────────────────────────────────────────

section "uv (Python package manager)"
if command -v uv &>/dev/null; then
  info "uv already installed ($(uv --version))."
else
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  info "uv installed ($(uv --version))."
fi

section "supervisord (process manager)"
if command -v supervisord &>/dev/null; then
  info "supervisord already installed."
else
  uv tool install supervisor
  info "supervisord installed."
fi

section "systemd user unit (lyra auto-start)"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT_FILE="$UNIT_DIR/lyra.service"
mkdir -p "$UNIT_DIR"
if [ -f "$UNIT_FILE" ]; then
  info "lyra.service already exists."
else
  cat > "$UNIT_FILE" << 'UNIT'
[Unit]
Description=Lyra supervisord (hub, telegram, discord)
After=network-online.target nats.service
Wants=network-online.target
Requires=nats.service

[Service]
Type=forking
PIDFile=%h/projects/lyra/deploy/supervisor/supervisord.pid
ExecStart=%h/projects/lyra/deploy/supervisor/start.sh --all
ExecStop=%h/projects/lyra/deploy/supervisor/supervisorctl.sh shutdown
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
UNIT
  info "lyra.service created."
fi

# Enable linger so user services start without login session
loginctl enable-linger "$ADMIN_USER" 2>/dev/null || true
info "Linger enabled for $ADMIN_USER (services auto-start on boot)."

# Note: lyra-monitor.timer (health monitoring) is installed by `make register`
# in the lyra repo, not by provision.sh. It requires secrets in .env first.
# After setup: cd ~/projects/lyra && make register && make monitor enable

section "Node.js"
if command -v node &>/dev/null; then
  info "Node.js already installed ($(node --version))."
else
  curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
  sudo apt install -y nodejs
  info "Node.js installed ($(node --version))."
fi

section "Claude Code CLI"
if command -v claude &>/dev/null; then
  info "Claude CLI already installed."
else
  npm install -g @anthropic-ai/claude-code
  info "Claude CLI installed. Run 'claude' to authenticate."
fi

section "agent-browser (headless browser for Claude Code)"
if command -v agent-browser &>/dev/null; then
  info "agent-browser already installed."
else
  npm install -g agent-browser && agent-browser install
  info "agent-browser installed."
fi

# ── External tools ───────────────────────────────────────────────────────────

section "External tools (ADR-010: Install, Wrap, Declare)"

if command -v imagecli &>/dev/null; then
  info "imagecli already installed."
else
  out=$(uv tool install git+https://github.com/roxabi/imageCLI 2>&1) && info "imagecli installed." || warn "imagecli install failed: $out"
fi

# Google Workspace CLI — see issue #65.
if command -v gws &>/dev/null; then
  info "gws already installed."
else
  warn "gws not installed. See: https://github.com/googleworkspace/cli"
fi

# ── Git config ───────────────────────────────────────────────────────────────

section "Git config"
if git config --global user.name &>/dev/null; then
  info "Git user.name: $(git config --global user.name)"
else
  read -rp "Git user.name: " GIT_NAME
  git config --global user.name "$GIT_NAME"
fi
if git config --global user.email &>/dev/null; then
  info "Git user.email: $(git config --global user.email)"
else
  read -rp "Git user.email: " GIT_EMAIL
  git config --global user.email "$GIT_EMAIL"
fi

# ── Done ─────────────────────────────────────────────────────────────────────

section "Done"
info "Provisioning complete — admin: $ADMIN_USER, agent: $AGENT_USER"

if [ "${NEEDS_REBOOT:-false}" = true ]; then
  warn "NVIDIA drivers installed — reboot now: sudo reboot"
  warn "After reboot, continue with the next step."
else
  echo ""
  info "Next steps:"
  echo "  1. Clone lyra and run setup:"
  echo ""
  echo "     git clone git@github.com:Roxabi/lyra.git ~/projects/lyra"
  echo "     cd ~/projects/lyra && python3 deploy/setup.py"
  echo ""
  echo "  2. Enable auto-start on boot:"
  echo ""
  echo "     systemctl --user daemon-reload"
  echo "     systemctl --user enable lyra.service"
  echo ""
  echo "  3. Authenticate Claude CLI:"
  echo ""
  echo "     claude"
  echo ""
  echo "  Recommended — NATS setup for multi-machine production (embedded nats-server covers dev/single-machine use):"
  echo ""
  echo "     cd ~/projects/lyra && make nats-install"
  echo ""
fi
