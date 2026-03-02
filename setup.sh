#!/usr/bin/env bash
# Machine 1 — Post-install setup script
# Usage: curl -fsSL https://raw.githubusercontent.com/Roxabi/lyra/main/setup.sh | bash
#        curl -fsSL https://raw.githubusercontent.com/Roxabi/lyra/main/setup.sh | ADMIN_USER=yourname bash
set -euo pipefail

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
info "Running setup for user: $ADMIN_USER"

section "System update"
sudo apt update && sudo apt upgrade -y

section "Base packages"
sudo apt install -y \
  curl wget git htop nvtop \
  fail2ban ufw \
  build-essential

section "NVIDIA drivers"
if nvidia-smi &>/dev/null; then
  warn "NVIDIA drivers already installed, skipping."
else
  sudo apt install -y nvidia-driver-550
  warn "Reboot required after script finishes to activate NVIDIA drivers."
  NEEDS_REBOOT=true
fi

section "SSH hardening"
sudo tee /etc/ssh/sshd_config.d/lyra.conf > /dev/null << 'EOF'
PermitRootLogin no
PasswordAuthentication no
PubkeyAuthentication yes
EOF
sudo systemctl restart ssh
info "SSH: password auth disabled, key-only."

section "Firewall (ufw)"
sudo ufw --force reset
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh
sudo ufw --force enable
info "UFW: only SSH allowed inbound."

section "fail2ban"
sudo systemctl enable fail2ban
sudo systemctl start fail2ban
info "fail2ban active."

section "GRUB — default Linux"
if ! grep -q "GRUB_DISABLE_OS_PROBER=false" /etc/default/grub; then
  echo 'GRUB_DISABLE_OS_PROBER=false' | sudo tee -a /etc/default/grub > /dev/null
fi
sudo sed -i 's/^GRUB_DEFAULT=.*/GRUB_DEFAULT=0/' /etc/default/grub
sudo sed -i 's/^GRUB_TIMEOUT=.*/GRUB_TIMEOUT=5/' /etc/default/grub
sudo update-grub
info "GRUB: Linux default, Windows detectable."

section "lyra agent account"
if id lyra &>/dev/null; then
  warn "User 'lyra' already exists, skipping."
else
  sudo useradd -m -s /bin/rbash -c "Lyra AI agent" lyra
  sudo passwd -l lyra
  sudo mkdir -p /home/lyra/.ssh
  sudo chmod 700 /home/lyra/.ssh
  sudo chown -R lyra:lyra /home/lyra/.ssh
  sudo chmod 750 /home/"$ADMIN_USER"
  info "User 'lyra' created (rbash, no sudo, isolated home)."
  warn "Add your agent SSH public key to /home/lyra/.ssh/authorized_keys"
fi

section "Done"
info "Setup complete for '$ADMIN_USER'."
if [ "${NEEDS_REBOOT:-false}" = true ]; then
  warn "NVIDIA drivers installed — reboot now: sudo reboot"
fi
