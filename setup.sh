#!/usr/bin/env bash
# Machine 1 — Post-install setup script
# Usage: curl -fsSL https://raw.githubusercontent.com/MickaelV0/lyra/main/setup.sh | bash
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()    { echo -e "${GREEN}[+]${NC} $1"; }
warn()    { echo -e "${YELLOW}[!]${NC} $1"; }
error()   { echo -e "${RED}[x]${NC} $1"; exit 1; }
section() { echo -e "\n${GREEN}=== $1 ===${NC}"; }

section "System update"
sudo apt update && sudo apt upgrade -y

section "Base packages"
sudo apt install -y \
  curl wget git htop nvtop \
  fail2ban ufw \
  build-essential

section "NVIDIA drivers (RTX 3080)"
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

section "Done"
info "Setup complete."
if [ "${NEEDS_REBOOT:-false}" = true ]; then
  warn "NVIDIA drivers installed — reboot now: sudo reboot"
fi
