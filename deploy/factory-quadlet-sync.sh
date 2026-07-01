#!/usr/bin/env bash
# deploy/factory-quadlet-sync.sh — auto-pull staging and trigger make converge
#
# Called by factory-quadlet-sync.service (systemd user unit).
# If HEAD differs from origin/staging, pulls and calls make converge
# (change-gated; no-op if already converged).
set -euo pipefail

cd ~/projects/roxabi-factory || {
    echo "ERROR: ~/projects/roxabi-factory not found" >&2
    exit 1
}

# Source shared library for PATH setup
source "$(dirname "$0")/lib/deploy-common.sh"

# Retry git fetch with backoff — a transient WiFi/DNS blip on M1 (Livebox IPv6/EDNS0
# flap, 2026-07-01) must not escalate a poll tick into a hard failure + deploy-failure
# alert. On persistent failure, SKIP this tick (exit 0); the next timer tick retries.
# A sustained real outage still surfaces via missed converges, just without alert spam.
_fetch_ok=0
for _attempt in 1 2 3; do
    if git fetch origin staging 2>/dev/null; then
        _fetch_ok=1
        break
    fi
    [ "${_attempt}" -lt 3 ] && sleep $((_attempt * 5))
done
if [ "${_fetch_ok}" -ne 1 ]; then
    echo "WARN: git fetch origin staging failed after 3 attempts (transient network?) — skipping tick" >&2
    exit 0
fi

if [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/staging)" ]; then
    echo "Already at origin/staging — nothing to do."
    exit 0
fi

require_clean_tree "${FACTORY_DIR}"
git pull --ff-only origin staging || {
    echo "ERROR: git pull --ff-only failed" >&2
    exit 1
}

echo "HEAD changed — running make converge ..."
make converge
