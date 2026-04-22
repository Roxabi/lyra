#!/usr/bin/env bash
# Lyra by Roxabi — Machine 1 post-install provisioning script
# Usage: curl -fsSL https://raw.githubusercontent.com/Roxabi/lyra/staging/deploy/provision.sh | bash
#        curl -fsSL https://raw.githubusercontent.com/Roxabi/lyra/staging/deploy/provision.sh | ADMIN_USER=yourname bash
#        curl -fsSL https://raw.githubusercontent.com/Roxabi/lyra/staging/deploy/provision.sh | ADMIN_USER=yourname AGENT_USER=myagent bash
set -euo pipefail
# Pin locale so [a-z] / [0-9] regex classes are ASCII-only regardless of host locale.
export LC_ALL=C

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
# Validate usernames — reject shell metachars since values are env-driven (curl|bash).
# Matches POSIX NAME_REGEX used by useradd: [a-z_][a-z0-9_-]* (max 32 chars).
USER_RE='^[a-z_][a-z0-9_-]{0,31}$'
[[ "$ADMIN_USER" =~ $USER_RE ]] || error "Invalid ADMIN_USER: $ADMIN_USER"
[[ "$AGENT_USER" =~ $USER_RE ]] || error "Invalid AGENT_USER: $AGENT_USER"
# Resolve admin home + UID from passwd — do not assume /home/$ADMIN_USER.
ADMIN_HOME=$(getent passwd "$ADMIN_USER" | cut -d: -f6)
[[ -n "$ADMIN_HOME" && -d "$ADMIN_HOME" ]] || error "Could not resolve home directory for $ADMIN_USER."
ADMIN_UID=$(id -u "$ADMIN_USER")
info "Running setup for admin: $ADMIN_USER (uid: $ADMIN_UID, home: $ADMIN_HOME), agent: $AGENT_USER"

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

# ── Container runtime ────────────────────────────────────────────────────────

section "Podman"
if command -v podman &>/dev/null; then
  info "podman already installed ($(podman --version))."
else
  # Ubuntu 26.04 LTS ships podman 5.x — Quadlet generator included natively.
  # Belt-and-suspenders on explicit deps (most are already pulled in by podman):
  #   uidmap         — rootless UID namespace mapping (hard dep of podman ≥4.5).
  #   fuse-overlayfs — fallback overlay driver when kernel overlayfs is
  #                    unavailable to unprivileged users (26.04 kernel has it,
  #                    but leave as safety net for older HWE kernels).
  #   slirp4netns    — legacy rootless networking; podman 5.x defaults to
  #                    `pasta` and slirp4netns is deprecated but still usable.
  sudo apt install -y podman uidmap fuse-overlayfs slirp4netns
  info "podman installed ($(podman --version))."
fi

# Verify rootless uid/gid ranges — required for user namespace mapping.
# Missing or too-small ranges (podman requires ≥65536 IDs) when user was created
# via `useradd` without -U/-m, or via a manual `usermod --add-subuids` with a
# smaller range. The grep-only "presence" check would accept `user:100000:100`.
has_sufficient_subids() {
  awk -F: -v u="$ADMIN_USER" '$1 == u && $3 >= 65536 {found=1} END {exit !found}' "$1"
}
if ! has_sufficient_subids /etc/subuid || ! has_sufficient_subids /etc/subgid; then
  warn "subuid/subgid ranges missing or too small for $ADMIN_USER — adding 100000-165535."
  sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535 "$ADMIN_USER"
  # Re-run migrate in case podman already has stale rootless state.
  # Guard against silent storage remap on partial re-runs: if the admin user
  # already has rootless images or named volumes, `podman system migrate` can
  # orphan data (volume files keep their old subuid ownership after the user
  # namespace is rebased). Require an explicit FORCE_MIGRATE=1 opt-in when
  # prior state is detected; otherwise hard-fail so the operator cannot miss
  # the signal in a `curl | bash` run where warnings scroll off screen.
  if sudo -u "$ADMIN_USER" HOME="$ADMIN_HOME" XDG_RUNTIME_DIR="/run/user/$ADMIN_UID" \
       sh -c 'podman images -q 2>/dev/null | grep -q . \
              || podman volume ls -q 2>/dev/null | grep -q .'; then
    if [[ "${FORCE_MIGRATE:-0}" != "1" ]]; then
      warn "Rootless storage for $ADMIN_USER already has images or volumes."
      warn "subuid range was just changed; migrating would risk orphaning existing data."
      error "Refusing to run \`podman system migrate\`. Set FORCE_MIGRATE=1 to proceed knowingly."
    else
      sudo -u "$ADMIN_USER" HOME="$ADMIN_HOME" XDG_RUNTIME_DIR="/run/user/$ADMIN_UID" \
        podman system migrate
    fi
  else
    # No prior rootless state detected — migrate is safe to run loud so real
    # failures (e.g. broken graph driver, missing newuidmap) surface under
    # `set -euo pipefail` instead of being silently swallowed.
    sudo -u "$ADMIN_USER" HOME="$ADMIN_HOME" XDG_RUNTIME_DIR="/run/user/$ADMIN_UID" \
      podman system migrate
  fi
  info "subuid/subgid added for $ADMIN_USER (≥65536 IDs)."
else
  info "subuid/subgid already configured for $ADMIN_USER (≥65536 IDs)."
fi

# Ensure user-scope container + systemd config dirs exist.
sudo -u "$ADMIN_USER" mkdir -p "$ADMIN_HOME/.config/containers/systemd"
sudo -u "$ADMIN_USER" mkdir -p "$ADMIN_HOME/.config/systemd/user"
info "Container dirs present: ~/.config/containers/systemd/ and ~/.config/systemd/user/"

# Enable linger so /run/user/$UID persists across logout for systemctl --user
# calls below. Hard-fail if this doesn't work — every downstream systemctl --user
# call depends on it; silent failure would only surface as cryptic "Failed to
# connect to bus" errors later.
loginctl enable-linger "$ADMIN_USER"
info "Linger enabled for $ADMIN_USER."

# Poll for /run/user/$ADMIN_UID — systemd-logind may create it asynchronously
# after enable-linger, and on a fresh headless boot it may not exist yet.
# Bounded wait (≤10s); bail out loud if it never materialises.
for _ in $(seq 10); do
  [[ -d "/run/user/$ADMIN_UID" ]] && break
  sleep 1
done
[[ -d "/run/user/$ADMIN_UID" ]] || error "/run/user/$ADMIN_UID never appeared — logind/linger not functional."

# Enable + start the rootless Podman API socket.
# XDG_RUNTIME_DIR is required when running systemctl --user via sudo;
# without it systemd cannot locate the user's dbus session.
if sudo -u "$ADMIN_USER" XDG_RUNTIME_DIR="/run/user/$ADMIN_UID" \
     systemctl --user is-enabled podman.socket &>/dev/null; then
  info "podman.socket already enabled for $ADMIN_USER."
else
  sudo -u "$ADMIN_USER" XDG_RUNTIME_DIR="/run/user/$ADMIN_UID" \
    systemctl --user enable --now podman.socket
  info "podman.socket enabled and started for $ADMIN_USER."
fi

# Reload user systemd so the Quadlet generator picks up any new .container units.
sudo -u "$ADMIN_USER" XDG_RUNTIME_DIR="/run/user/$ADMIN_UID" \
  systemctl --user daemon-reload
info "User systemd daemon reloaded (Quadlet generator active)."

# Smoke tests — soft-fail so a transient network error doesn't abort provisioning
# before SSH hardening / firewall run.
if sudo -u "$ADMIN_USER" podman info --format '{{.Version.Version}}' > /dev/null 2>&1; then
  info "podman info OK"
else
  warn "podman info failed — rootless setup may be incomplete (check subuid/subgid)."
fi

if sudo -u "$ADMIN_USER" podman images --format '{{.Repository}}' \
     | grep -q "^quay.io/podman/hello$"; then
  info "hello-world image already pulled, skipping smoke test."
else
  if sudo -u "$ADMIN_USER" podman run --rm quay.io/podman/hello > /dev/null; then
    info "podman hello-world smoke OK"
  else
    warn "hello-world pull failed — check network / quay.io reachability."
  fi
fi

warn "Remember to update local/machines.md with: Podman $(podman --version 2>/dev/null | awk '{print $3}' || echo 'N/A')"

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
  sudo chmod 750 "$ADMIN_HOME"
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

# Linger is already enabled (and verified) in the Podman section above —
# do not call it again here. A duplicate `loginctl enable-linger ... || true`
# would silently log success even if the real call had failed, creating false
# confidence about persistent user services.

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
  echo "     cd ~/projects/lyra && make nats-setup"
  echo ""
fi
