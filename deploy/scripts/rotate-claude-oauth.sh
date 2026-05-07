#!/usr/bin/env bash
# Lyra Claude Code OAuth token rotation — replaces the lyra-claude-oauth Podman
# secret and restarts lyra-clipool so the new env var is picked up.
#
# Why a clipool restart: Podman Secret=type=env binds the secret value to the
# container's environment at start time. Unlike type=mount (file backed by
# tmpfs that the container re-reads), env vars are immutable for the lifetime
# of the container — `secret create --replace` alone has no effect on a
# running clipool.
#
# WARNING: in-flight `claude` subprocesses spawned by clipool are killed on
# restart. Active conversations lose their --resume session id and the user
# sees a brief stall while clipool re-spawns. Schedule rotations during low
# traffic. The 1-year setup-token TTL means this is at most an annual event.
#
# Usage: rotate-claude-oauth.sh /abs/path/to/new-token-file
# Token format: single line, no trailing newline. Generate with
#   `claude setup-token > /tmp/claude-oauth.tok` on an interactive workstation.
#
# Acceptance (#1108): ≤5s end-to-end from `secret rm` to lyra-clipool serving
# the new token. Measured 2026-05-07 on M₁ (Podman 5.7.0): 0.617s.
set -euo pipefail
export LC_ALL=C
# Required when invoked via `make remote` (SSH non-interactive shell): without
# it, `systemctl --user` fails to locate the dbus session ("Failed to connect
# to bus: No such file or directory"). Provision.sh sets this consistently;
# the rotate scripts must too.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

NEW_TOKEN="${1:-}"
TOKEN_PATH_RE='^[A-Za-z0-9._/-]+$'
[[ -n "$NEW_TOKEN" ]] || { echo "usage: $0 /path/to/new-token-file" >&2; exit 2; }
[[ "$NEW_TOKEN" =~ $TOKEN_PATH_RE ]] || { echo "Invalid token path: $NEW_TOKEN" >&2; exit 2; }
[[ -f "$NEW_TOKEN" ]] || { echo "Token file not found: $NEW_TOKEN" >&2; exit 2; }
chmod 600 "$NEW_TOKEN"
token_mode=$(stat -c '%a' "$NEW_TOKEN")
[[ "$token_mode" == "600" ]] || { echo "Token file must be mode 0600 (got $token_mode): $NEW_TOKEN" >&2; exit 2; }

# Tolerate first-time creation: rm only if exists.
if podman secret inspect lyra-claude-oauth &>/dev/null; then
  podman secret rm lyra-claude-oauth
fi
# Pipe via `tr -d '\n'` so a trailing newline (common from `cmd > file`) cannot
# leak into the secret value and silently break auth at runtime.
tr -d '\n' < "$NEW_TOKEN" | podman secret create lyra-claude-oauth -
# Wipe source file — best-effort. shred is a no-op on CoW filesystems
# (btrfs, tmpfs, ZFS); rely on encrypted home for at-rest protection.
shred -u "$NEW_TOKEN" || rm -f "$NEW_TOKEN"
systemctl --user restart lyra-clipool.service

# Gate on clipool Up — env vars are picked up at container start, so once the
# container reports Up the new token is in effect. Bound the wait at 30s
# (60×0.5s) — clipool has heavier startup than the gh-helper sidecar.
for _ in $(seq 60); do
  if podman ps --filter name=lyra-clipool --format '{{.Status}}' \
       | grep -q '^Up '; then
    echo "lyra-clipool restarted, secret rotated."
    exit 0
  fi
  sleep 0.5
done
echo "rotation did not complete within 30s" >&2
exit 1
