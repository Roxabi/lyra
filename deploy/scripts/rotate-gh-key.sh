#!/usr/bin/env bash
# Lyra GitHub App PEM rotation — replaces the lyra-gh-pem Podman secret and
# restarts the helper container that mounts it.
#
# Sidecar Pod design (post #1078): the PEM is consumed only by lyra-gh-helper
# (uid 1501). lyra-clipool reads tokens via the dispenser socket, never the PEM,
# so it does NOT need to restart for a key rotation. The downtime budget covers
# the helper restart window during which the dispenser socket is briefly absent.
#
# WARNING: in-flight git/gh operations that already hold a minted token continue
# uninterrupted. New token requests during the helper restart window get
# ECONNREFUSED on the dispenser socket. Retry-on-rotate is the caller's
# responsibility — this script does NOT drain in-flight operations.
#
# Usage: rotate-gh-key.sh /abs/path/to/new.pem
#
# Acceptance (#1078 AC#15): ≤10s end-to-end from `secret rm` to dispenser
# socket reachable from inside lyra-clipool. Measured 2026-05-06 on M₁
# (Podman 5.7.0): 0.58s.
set -euo pipefail
export LC_ALL=C
# Required when invoked via `make remote` (SSH non-interactive shell): without
# it, `systemctl --user` fails to locate the dbus session ("Failed to connect
# to bus: No such file or directory"). Provision.sh sets this consistently;
# the rotate scripts must too.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

NEW_PEM="${1:-}"
[[ -n "$NEW_PEM" ]] || { echo "usage: $0 /path/to/new.pem" >&2; exit 2; }
# Resolve symlinks and eliminate any '..' components before existence check;
# this blocks path-traversal via '..' sequences (issue #1118).
RESOLVED=$(realpath -e "$NEW_PEM" 2>/dev/null) \
  || { echo "PEM file not found or unresolvable: $NEW_PEM" >&2; exit 2; }
# Reject paths outside trusted directories.
[[ "$RESOLVED" == /home/lyra/secrets/* || "$RESOLVED" == /etc/lyra/* ]] \
  || { echo "PEM path outside trusted dirs (/home/lyra/secrets/, /etc/lyra/): $RESOLVED" >&2; exit 2; }

# Tolerate first-time creation: rm only if exists.
if podman secret inspect lyra-gh-pem &>/dev/null; then
  podman secret rm lyra-gh-pem
fi
podman secret create lyra-gh-pem "$NEW_PEM"
systemctl --user restart lyra-gh-helper.service

# Gate on helper Up AND dispenser socket reachable from clipool — the latter is
# the actual user-visible criterion (clipool must be able to mint tokens again).
# Bound the wait at 10s (20×0.5s).
for _ in $(seq 20); do
  if podman ps --filter name=lyra-gh-helper --format '{{.Status}}' \
       | grep -q '^Up ' \
     && podman exec lyra-clipool test -S /run/lyra-gh-token/dispenser.sock \
          2>/dev/null; then
    echo "lyra-gh-helper restarted, dispenser reachable, secret rotated."
    exit 0
  fi
  sleep 0.5
done
echo "rotation did not complete within 10s" >&2
exit 1
