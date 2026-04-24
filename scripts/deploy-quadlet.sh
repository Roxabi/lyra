#!/bin/bash
# Deploy script for production — Quadlet/podman path.
# Pulls latest code, rebuilds the lyra image, restarts systemd user services.
# Called by: systemd timer (auto) or manual invocation on M₁.
#
# Checks two repos:
# - lyra (main project) — always checked; on new commits: test → build → restart containers
# - voiceCLI (voice services) — checked independently; baked into the image, triggers rebuild
#
# Does NOT touch nats.service — managed independently (nkey regen flow, DEPLOYMENT.md §10).
set -euo pipefail
umask 0077
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

export PATH="$HOME/.local/bin:$PATH"
source "$HOME/.local/bin/env" 2>/dev/null || true  # uv

# ── Project variables ─────────────────────────────────────────────────────────

PROJECT="lyra"
PROJECT_DIR="$HOME/projects/lyra"
PROJECT_BRANCH="staging"
IMAGE="localhost/lyra:latest"
DOCKERFILE="Dockerfile"
HUB_SERVICE="lyra-hub"
ADAPTER_SERVICES="lyra-telegram lyra-discord"
ENV_FILES_DIR="$HOME/.lyra/env"
ENV_FILES="hub telegram discord"
LOG_FILE="$HOME/.local/state/lyra/logs/deploy.log"
FAIL_FILE="$HOME/.local/state/lyra/deploy_failed_shas.txt"
PROJECT_TEST_CMD="uv run pytest --tb=short -q"

# ── voiceCLI extra repo ───────────────────────────────────────────────────────
# voiceCLI is baked into the lyra image; pulling a new version triggers a rebuild.
# After pulling, uv sync --upgrade-package voicecli refreshes the lyra venv.

_voicecli_upgrade_hook() {
    cd "$PROJECT_DIR"
    timeout 60 uv sync --all-extras --upgrade-package voicecli 2>&1 | tee -a "$LOG_FILE"
}

# Newline-separated; each line: "name:path:hook"
EXTRA_REPOS="voiceCLI:$HOME/projects/voiceCLI:_voicecli_upgrade_hook"

# ── Source library and run ────────────────────────────────────────────────────
# Prefer in-repo copy (dev mode), fall back to installed copy.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB_PATH="$SCRIPT_DIR/deploy-lib.sh"
[[ -f "$LIB_PATH" ]] || LIB_PATH="$HOME/.local/lib/roxabi/deploy-lib.sh"
[[ -f "$LIB_PATH" ]] || {
    echo "ERROR: deploy-lib.sh not found at $SCRIPT_DIR or $HOME/.local/lib/roxabi/" >&2
    exit 1
}
source "$LIB_PATH"
run_deploy "$@"
