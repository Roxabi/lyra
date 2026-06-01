#!/usr/bin/env bash
# deploy/lib/deploy-common.sh — shared library for deploy scripts
#
# Usage: source "$(dirname "$0")/../lib/deploy-common.sh"

set -euo pipefail

# ── PATH setup ───────────────────────────────────────────────────────────────
# %h in systemd unit specifiers maps to $HOME in shell.
export PATH="${HOME}/projects/lyra/.venv/bin:${HOME}/.local/bin:${PATH}"

# ── Environment guards ─────────────────────────────────────────────────────
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"

# ── Constants ────────────────────────────────────────────────────────────────
LYRA_DIR="${HOME}/projects/lyra"
CONVERGE_STAMP="${HOME}/.roxabi/factory/.converge-stamp"
QUADLET_DIR="${HOME}/.config/containers/systemd"
LYRA_NKEYS_DIR="${HOME}/.roxabi/factory/nkeys"
DEPLOY_LOCK="/run/user/$(id -u)/lyra-deploy.lock"

# ── flock wrapper ────────────────────────────────────────────────────────────
# Run a command under an exclusive lock. Exit 0 (no error) if the lock is held.
# Usage: with_deploy_lock <command> [args...]
with_deploy_lock() {
    exec 200>"${DEPLOY_LOCK}"
    if ! flock -n 200; then
        echo "Deploy lock held at ${DEPLOY_LOCK} — another converge is running."
        exit 0
    fi
    "$@"
}

# ── Change detection helpers ─────────────────────────────────────────────────

# Compute current convergence fingerprint: git HEAD + unit checksums + auth.conf SHA
# (+ voiceCLI HEAD if present).
# Output format: <git-head>:<units-sha256>:<authconf-sha256>[:<voicecli-head>]
compute_convergence_state() {
    local git_head unit_sha auth_sha voicecli_head

    git_head=$(cd "${LYRA_DIR}" && git rev-parse HEAD 2>/dev/null || echo "none")

    if [ -d "${QUADLET_DIR}" ]; then
        unit_sha=$(find "${QUADLET_DIR}" -maxdepth 1 \( -name 'lyra*' -o -name 'factory*' \) -type f -print0 \
            | sort -z | xargs -0 -r sha256sum | sha256sum | awk '{print $1}')
    else
        unit_sha="none"
    fi

    if [ -f "${LYRA_NKEYS_DIR}/auth.conf" ]; then
        auth_sha=$(sha256sum "${LYRA_NKEYS_DIR}/auth.conf" | awk '{print $1}')
    else
        auth_sha="none"
    fi

    VOICE_DIR="${VOICE_DIR:-${HOME}/projects/voiceCLI}"
    if [ -d "${VOICE_DIR}/.git" ]; then
        voicecli_head=$(cd "${VOICE_DIR}" && git rev-parse HEAD 2>/dev/null || echo "none")
    else
        voicecli_head="none"
    fi

    echo "${git_head}:${unit_sha}:${auth_sha}:${voicecli_head}"
}

# Read the last recorded convergence state.
read_convergence_state() {
    if [ -f "${CONVERGE_STAMP}" ]; then
        cat "${CONVERGE_STAMP}"
    else
        echo "none"
    fi
}

# Write the current convergence state to the stamp file.
write_convergence_state() {
    mkdir -p "$(dirname "${CONVERGE_STAMP}")"
    compute_convergence_state > "${CONVERGE_STAMP}"
}

# Return 0 if the system is already converged (current == last recorded).
is_converged() {
    local current last
    current=$(compute_convergence_state)
    last=$(read_convergence_state)
    [ "${current}" = "${last}" ]
}
