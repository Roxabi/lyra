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

git fetch origin staging || {
    echo "ERROR: git fetch origin staging failed" >&2
    exit 1
}

if [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/staging)" ]; then
    echo "Already at origin/staging — nothing to do."
    exit 0
fi

git pull --ff-only origin staging || {
    echo "ERROR: git pull --ff-only failed" >&2
    exit 1
}

echo "HEAD changed — running make converge ..."
make converge
