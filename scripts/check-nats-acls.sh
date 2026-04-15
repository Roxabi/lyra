#!/usr/bin/env bash
# Poll journalctl for NATS permission violations after an ACL reload.
#
# Usage: scripts/check-nats-acls.sh [--since <timestamp>] [--window <seconds>]
# Env:   NATS_UNIT=nats.service   (override systemd unit name; matches deploy/nats/nats.service)
set -euo pipefail
SINCE=""
WINDOW="${WINDOW:-90}"
NATS_UNIT="${NATS_UNIT:-nats.service}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --since)  SINCE="$2"; shift 2 ;;
    --window) WINDOW="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done
[ -n "$SINCE" ] || SINCE=$(date -u -d '-10 seconds' +'%Y-%m-%d %H:%M:%S')
DEADLINE=$(( $(date +%s) + WINDOW ))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  if journalctl -u "$NATS_UNIT" --since "$SINCE" --no-pager 2>/dev/null \
       | grep -q 'Permissions Violation'; then
    echo "FAIL: Permissions Violation detected in $NATS_UNIT since $SINCE" >&2
    journalctl -u "$NATS_UNIT" --since "$SINCE" --no-pager | grep 'Permissions Violation' | tail -20
    exit 1
  fi
  sleep 2
done
echo "OK: no Permissions Violation in $NATS_UNIT over ${WINDOW}s window"
exit 0
