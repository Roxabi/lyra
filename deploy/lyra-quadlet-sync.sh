#!/usr/bin/env bash
# deploy/lyra-quadlet-sync.sh — auto-pull staging and conditional quadlet-install
#
# Called by lyra-quadlet-sync.service (systemd user unit).
# Guard: only runs make quadlet-install when deploy/quadlet/**, Makefile, or
# tools/render_quadlet.py changed.
set -euo pipefail

cd ~/projects/lyra || {
    echo "ERROR: ~/projects/lyra not found" >&2
    exit 1
}

git fetch origin staging || {
    echo "ERROR: git fetch origin staging failed" >&2
    exit 1
}

if [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/staging)" ]; then
    echo "Already at origin/staging — nothing to do."
    exit 0
fi

PRE_SHA=$(git rev-parse HEAD)
git pull --ff-only origin staging || {
    echo "ERROR: git pull --ff-only failed" >&2
    exit 1
}

if git diff --name-only "$PRE_SHA" HEAD | grep -qE '^(deploy/quadlet/|Makefile|tools/render_quadlet\.py)'; then
    echo "Quadlet-relevant files changed — running make quadlet-install ..."
    export PATH="$HOME/.local/bin:$PATH"
    make quadlet-install
else
    echo "No Quadlet-relevant changes — skipping make quadlet-install."
fi
