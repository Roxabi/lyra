#!/usr/bin/env bash
# deploy/lib/deploy-common.sh — shared library for deploy scripts
#
# Usage: source "$(dirname "$0")/../lib/deploy-common.sh"

set -euo pipefail

# ── PATH setup ───────────────────────────────────────────────────────────────
# %h in systemd unit specifiers maps to $HOME in shell.
export PATH="${HOME}/projects/roxabi-factory/.venv/bin:${HOME}/.local/bin:${PATH}"

# ── Environment guards ─────────────────────────────────────────────────────
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
# shellcheck source=operator-log.sh
source "$(dirname "${BASH_SOURCE[0]}")/operator-log.sh"

# ── Constants ────────────────────────────────────────────────────────────────
FACTORY_DIR="${HOME}/projects/roxabi-factory"
CONVERGE_STAMP="${HOME}/.roxabi/factory/.converge-stamp"
QUADLET_DIR="${HOME}/.config/containers/systemd"
FACTORY_NKEYS_DIR="${HOME}/.roxabi/factory/nkeys"
DEPLOY_LOCK="/run/user/$(id -u)/factory-deploy.lock"
# Tracked factory images — digests are field 5–6 of the convergence fingerprint.
FACTORY_TRACKED_IMAGES=(
    "ghcr.io/roxabi/factory:staging-svc"
    "ghcr.io/roxabi/factory:staging"
)

# ── flock wrapper ────────────────────────────────────────────────────────────
# Run a command under an exclusive lock. Exit 0 (no error) if the lock is held.
# Usage: with_deploy_lock <command> [args...]
with_deploy_lock() {
    exec 200>"${DEPLOY_LOCK}"
    if ! flock -n 200; then
        echo "Deploy lock held at ${DEPLOY_LOCK} — another converge is running."
        op_log converge_lock_held lock="${DEPLOY_LOCK}"
        exit 0
    fi
    "$@"
}

# ── Dirty-tree guard ─────────────────────────────────────────────────────────
# Abort before a `git pull --ff-only` if the checkout has uncommitted *tracked*
# changes. Without this, a dev-on-prod edit (e.g. the 2026-06-14 WIP incident)
# makes every 5-min auto-deploy pull fail mid-pipeline — a silent jam. Untracked
# files are ignored (they do not block ff-only); only tracked mods are fatal.
require_clean_tree() {
    local dir="$1"
    if ! git -C "${dir}" diff --quiet HEAD 2>/dev/null; then
        echo "ERROR: ${dir} has uncommitted tracked changes — refusing to pull" >&2
        echo "       (a dirty prod checkout jams ff-only auto-deploy). Resolve manually:" >&2
        echo "       git -C ${dir} status --short" >&2
        exit 1
    fi
}

# ── Change detection helpers ─────────────────────────────────────────────────

# Strip registry ref prefix and sha256: — converge stamp stores bare hex only.
factory_normalize_digest() {
    sed 's/.*@//' | sed 's/^sha256://'
}

# All local registry digests for an image (bare hex, one per line).
factory_local_repo_digests() {
    podman image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$1" 2>/dev/null \
        | factory_normalize_digest || true
}

# Remote OCI index digest (bare hex). Retries optional (default 1).
factory_remote_index_digest() {
    local image="$1" max_attempts="${2:-1}" attempt delay out digest
    for attempt in $(seq 1 "${max_attempts}"); do
        if out=$(skopeo inspect "docker://${image}" 2>/dev/null); then
            digest=$(printf '%s' "${out}" | jq -r '.Digest' | factory_normalize_digest)
            if [ -n "${digest}" ] && [ "${digest}" != "null" ]; then
                echo "${digest}"
                return 0
            fi
        fi
        if [ "${attempt}" -lt "${max_attempts}" ]; then
            delay=$(( attempt * 2 ))
            echo "skopeo inspect failed (attempt ${attempt}/${max_attempts}), retrying in ${delay}s..." >&2
            sleep "${delay}"
        fi
    done
    echo "skopeo inspect failed after ${max_attempts} attempt(s) for ${image}" >&2
    return 1
}

# SSOT for stamp fields 4–5 and post-autoupdate drift checks (#1749).
# Prefers the skopeo index digest when it appears in local RepoDigests; otherwise
# falls back to the first RepoDigest entry (skopeo unavailable or image stale).
factory_canonical_image_digest() {
    local image="$1" digests remote
    digests=$(factory_local_repo_digests "${image}")
    if [ -z "${digests}" ]; then
        echo "none"
        return 0
    fi
    if remote=$(factory_remote_index_digest "${image}" 1 2>/dev/null) && [ -n "${remote}" ]; then
        if echo "${digests}" | grep -Fxq "${remote}"; then
            echo "${remote}"
            return 0
        fi
    fi
    echo "${digests}" | head -n1
}

# Upgrade legacy 4-field stamps to the current 6-field schema.
_normalize_convergence_fingerprint() {
    local fp="${1}"
    local n
    n=$(awk -F: '{print NF}' <<< "${fp}")
    case "${n}" in
        4) echo "${fp}:none:none" ;;
        6) echo "${fp}" ;;
        *) echo "${fp}" ;;
    esac
}

# Compute current convergence fingerprint: git HEAD + unit checksums + auth.conf SHA
# + voiceCLI HEAD + tracked image index digests.
# Output format:
#   <git-head>:<units-sha256>:<authconf-sha256>:<voicecli-head>:<staging-svc-digest>:<staging-digest>
compute_convergence_state() {
    local git_head unit_sha auth_sha voicecli_head image_svc_sha image_stg_sha

    git_head=$(cd "${FACTORY_DIR}" && git rev-parse HEAD 2>/dev/null || echo "none")

    if [ -d "${QUADLET_DIR}" ]; then
        unit_sha=$(find "${QUADLET_DIR}" -maxdepth 1 -name 'factory*' -type f -print0 \
            | sort -z | xargs -0 -r sha256sum | sha256sum | awk '{print $1}')
    else
        unit_sha="none"
    fi

    if [ -f "${FACTORY_NKEYS_DIR}/auth.conf" ]; then
        auth_sha=$(sha256sum "${FACTORY_NKEYS_DIR}/auth.conf" | awk '{print $1}')
    else
        auth_sha="none"
    fi

    VOICE_DIR="${VOICE_DIR:-${HOME}/projects/voiceCLI}"
    if [ -d "${VOICE_DIR}/.git" ]; then
        voicecli_head=$(cd "${VOICE_DIR}" && git rev-parse HEAD 2>/dev/null || echo "none")
    else
        voicecli_head="none"
    fi

    image_svc_sha=$(factory_canonical_image_digest "${FACTORY_TRACKED_IMAGES[0]}")
    image_stg_sha=$(factory_canonical_image_digest "${FACTORY_TRACKED_IMAGES[1]}")

    echo "${git_head}:${unit_sha}:${auth_sha}:${voicecli_head}:${image_svc_sha}:${image_stg_sha}"
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

# Classify the kind of drift between a recorded stamp and the current state.
#
# Contract:
#   _classify_drift <last> <current>
#
#   Both arguments are fingerprints in the format produced by compute_convergence_state:
#     <git_head>:<unit_sha>:<auth_sha>:<voicecli_head>:<staging-svc-digest>:<staging-digest>
#   Legacy stamps omit the two image fields (4 colon-separated fields); they are
#   normalized to :none:none before comparison. Any field may be the sentinel "none".
#
# Stdout (one word):
#   none       — no change (current == last, or last == "none" sentinel meaning no stamp)
#   auth       — ONLY auth_sha (field 2) differs; all structural fields are identical
#   structural — at least one structural field differs (git, units, voice, image digests)
#
# The function always succeeds (exit 0); callers branch on stdout.
_classify_drift() {
    local last="${1}"
    local current="${2}"

    last=$(_normalize_convergence_fingerprint "${last}")
    current=$(_normalize_convergence_fingerprint "${current}")

    # Identical — no drift at all
    if [ "${last}" = "${current}" ]; then
        echo "none"
        return 0
    fi

    # No recorded stamp yet — treat as structural so a full converge runs
    if [ "${last}" = "none" ]; then
        echo "structural"
        return 0
    fi

    # Field-count guard: fingerprints must have exactly 6 colon-separated fields
    # after legacy normalization. Fail-safe to "structural".
    local last_fields cur_fields
    last_fields=$(awk -F: '{print NF}' <<< "${last}")
    cur_fields=$(awk -F:  '{print NF}' <<< "${current}")
    if [ "${last_fields}" -ne 6 ] || [ "${cur_fields}" -ne 6 ]; then
        echo "structural"
        return 0
    fi

    local last_git last_unit last_auth last_voice last_img_svc last_img_stg
    local cur_git  cur_unit  cur_auth  cur_voice  cur_img_svc  cur_img_stg

    IFS=':' read -r last_git last_unit last_auth last_voice last_img_svc last_img_stg <<< "${last}"
    IFS=':' read -r cur_git  cur_unit  cur_auth  cur_voice  cur_img_svc  cur_img_stg  <<< "${current}"

    # Check structural fields first (image digests are structural — containers must restart)
    if [ "${last_git}"      != "${cur_git}"      ] \
    || [ "${last_unit}"     != "${cur_unit}"     ] \
    || [ "${last_voice}"    != "${cur_voice}"    ] \
    || [ "${last_img_svc}"  != "${cur_img_svc}"  ] \
    || [ "${last_img_stg}"  != "${cur_img_stg}"  ]; then
        echo "structural"
        return 0
    fi

    # Structural fields match; auth_sha is the only thing that can differ here
    # (we already ruled out full equality above).
    echo "auth"
    return 0
}
