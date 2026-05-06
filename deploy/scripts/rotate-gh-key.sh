#!/usr/bin/env bash
# Lyra GitHub App PEM rotation — replaces the lyra-gh-pem Podman secret and
# restarts lyra-clipool. Aim: ≤10s downtime end-to-end.
#
# WARNING: in-flight git/gh operations using the old token are TCP-reset on
# container restart; retry-on-rotate is the caller's responsibility. This
# script does NOT drain in-flight operations.
#
# Usage: rotate-gh-key.sh /abs/path/to/new.pem
#
# Exits non-zero if the new PEM is missing/invalid, or if lyra-clipool fails
# to return to "Up" state within 10s after restart.
set -euo pipefail
export LC_ALL=C

NEW_PEM="${1:-}"
PEM_RE='^[A-Za-z0-9._/-]+$'
[[ -n "$NEW_PEM" ]] || { echo "usage: $0 /path/to/new.pem" >&2; exit 2; }
[[ "$NEW_PEM" =~ $PEM_RE ]] || { echo "Invalid PEM path: $NEW_PEM" >&2; exit 2; }
[[ -f "$NEW_PEM" ]] || { echo "PEM file not found: $NEW_PEM" >&2; exit 2; }

# Tolerate first-time creation: rm only if exists.
if podman secret inspect lyra-gh-pem &>/dev/null; then
  podman secret rm lyra-gh-pem
fi
podman secret create lyra-gh-pem "$NEW_PEM"
systemctl --user restart lyra-clipool

# Wait for clipool to return to "Up" — clipool has no published port today, so
# we gate on container status as a placeholder. Bound the wait at 10s (20×0.5s).
for _ in $(seq 20); do
  if podman ps --filter name=lyra-clipool --format '{{.Status}}' \
       | grep -q '^Up '; then
    echo "lyra-clipool restarted, secret rotated."
    exit 0
  fi
  sleep 0.5
done
echo "lyra-clipool did not return to Up state within 10s" >&2
exit 1
