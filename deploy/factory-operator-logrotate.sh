#!/usr/bin/env bash
# deploy/factory-operator-logrotate.sh — rotate ~/.local/state/factory/logs/operator.log
# Invoked by factory-operator-logrotate.service (weekly systemd user timer).

set -euo pipefail

STATE="${XDG_STATE_HOME:-$HOME/.local/state}"
LOG_DIR="${STATE}/factory/logs"
LOG="${LOG_DIR}/operator.log"
STATUS="${LOG_DIR}/logrotate.status"
CONF_DIR="${HOME}/.config/logrotate"
CONF="${CONF_DIR}/factory-operator.conf"

mkdir -p "${LOG_DIR}" "${CONF_DIR}"

cat > "${CONF}" <<EOF
${LOG} {
    weekly
    rotate 12
    maxsize 10M
    missingok
    notifempty
    copytruncate
    compress
    delaycompress
}
EOF

if ! command -v logrotate >/dev/null 2>&1; then
    echo "WARN: logrotate not installed — skipping operator.log rotation" >&2
    exit 0
fi

logrotate -s "${STATUS}" "${CONF}"