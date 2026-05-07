#!/usr/bin/env bash
# deploy/lib/env.sh — shared environment guards for deploy scripts
#
# Source this file at the top of any deploy script that calls `systemctl --user`
# or `podman` via a non-interactive SSH session (e.g., `make remote`).
#
# Safe to source multiple times — all assignments use the :- no-op pattern.
#
# Usage (from a script in deploy/ or deploy/nats/):
#   source "$(dirname "$0")/../lib/env.sh"
#
# Usage (from a script in deploy/scripts/):
#   source "$(dirname "$0")/../lib/env.sh"

# Required when invoked via `make remote` (SSH non-interactive shell): without
# it, `systemctl --user` fails to locate the dbus session ("Failed to connect
# to bus: No such file or directory"). Provision.sh sets this consistently;
# all deploy scripts must too.
: "${XDG_RUNTIME_DIR:=/run/user/$(id -u)}"
export XDG_RUNTIME_DIR
