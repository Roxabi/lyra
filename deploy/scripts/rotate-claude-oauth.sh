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
# Acceptance (#1108): script gate is the 30s `podman ps` poll loop below
# (line 51-58). Measured 0.617s end-to-end on M₁ (Podman 5.7.0, 2026-05-07)
# from `secret rm` to lyra-clipool reporting `Up`.
set -euo pipefail
export LC_ALL=C
# shellcheck source=../lib/env.sh
source "$(dirname "$0")/../lib/env.sh"

NEW_TOKEN="${1:-}"
[[ -n "$NEW_TOKEN" ]] || { echo "usage: $0 /path/to/new-token-file" >&2; exit 2; }
# Resolve symlinks and eliminate any '..' components before existence check;
# this blocks path-traversal via '..' sequences (sister fix to #1118).
RESOLVED=$(realpath -e "$NEW_TOKEN" 2>/dev/null) \
  || { printf 'Token file not found or unresolvable: %q\n' "$NEW_TOKEN" >&2; exit 2; }
# Reject paths outside trusted directories.
[[ "$RESOLVED" == /home/lyra/secrets/* || "$RESOLVED" == /etc/lyra/* ]] \
  || { echo "Token path outside trusted dirs (/home/lyra/secrets/, /etc/lyra/): $RESOLVED" >&2; exit 2; }
chmod 600 "$RESOLVED"
token_mode=$(stat -c '%a' "$RESOLVED")
[[ "$token_mode" == "600" ]] || { echo "Token file must be mode 0600 (got $token_mode): $RESOLVED" >&2; exit 2; }

# Tolerate first-time creation: rm only if exists.
if podman secret inspect lyra-claude-oauth &>/dev/null; then
  podman secret rm lyra-claude-oauth
fi
# Pipe via `tr -d '\n'` so a trailing newline (common from `cmd > file`) cannot
# leak into the secret value and silently break auth at runtime.
tr -d '\n' < "$RESOLVED" | podman secret create lyra-claude-oauth -
# Wipe source file — best-effort. shred is a no-op on CoW filesystems
# (btrfs, tmpfs, ZFS); rely on encrypted home for at-rest protection.
shred -u "$RESOLVED" || rm -f "$RESOLVED"
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
