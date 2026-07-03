#!/usr/bin/env bash
# deploy/lib/deploy-common.sh — shared library for deploy scripts
#
# Usage: source "$(dirname "$0")/../lib/deploy-common.sh"

set -euo pipefail

# ── PATH setup ───────────────────────────────────────────────────────────────
# %h in systemd unit specifiers maps to $HOME in shell.
export PATH="${HOME}/projects/roxabi-factory/.venv/bin:${HOME}/.local/bin:${PATH}"

# ── uv lockfile guard ────────────────────────────────────────────────────────
# Every deploy script sources this lib and runs against the LIVE prod checkout.
# A bare `uv run`/`uv sync` there re-resolves and rewrites uv.lock whenever
# pyproject.toml has drifted from the committed lock (e.g. a dependabot `pip`
# bump merged without the lock regen), dirtying the tree and jamming
# factory-quadlet-sync's ff-only pull (12h M1 deploy outage, 2026-07-02).
# UV_FROZEN makes "never mutate the lock on the host" the deploy-path default,
# so a new script can't reintroduce the churn by forgetting `--frozen`.
# NB: UV_NO_SYNC does NOT prevent this — it skips the venv sync, not the lock
# refresh; only UV_FROZEN/`--frozen` does.
export UV_FROZEN=1

# ── Environment guards ─────────────────────────────────────────────────────
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
# shellcheck source=operator-log.sh
source "$(dirname "${BASH_SOURCE[0]}")/operator-log.sh"

# ── Constants ────────────────────────────────────────────────────────────────
FACTORY_DIR="${HOME}/projects/roxabi-factory"
PROJECTS_DIR="${PROJECTS_DIR:-${HOME}/projects}"
HOSTS_TOML="${HOSTS_TOML:-${PROJECTS_DIR}/hosts.toml}"
CLUSTER_PLAN="${CLUSTER_PLAN:-${PROJECTS_DIR}/lib/cluster_plan.py}"
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

# ── Host role guard (SSOT: ~/projects/hosts.toml via lib/cluster_plan.py) ───
require_host_role() {
    local required_role="${1:?require_host_role: role required}"
    local hostname="${2:-$(hostname)}"
    if [ ! -f "${HOSTS_TOML}" ]; then
        echo "ERROR: missing ${HOSTS_TOML} — cannot verify host role" >&2
        exit 1
    fi
    if [ ! -f "${CLUSTER_PLAN}" ]; then
        echo "ERROR: missing ${CLUSTER_PLAN} — cannot verify host role" >&2
        exit 1
    fi
    if ! python3 "${CLUSTER_PLAN}" --hosts-toml "${HOSTS_TOML}" has-role "${hostname}" "${required_role}"; then
        echo "ERROR: host '${hostname}' lacks required role '${required_role}' (${HOSTS_TOML})" >&2
        echo "       make converge is for factory-hub hosts (M₁ prod) only." >&2
        echo "       On other hosts use: ~/projects/deploy.sh" >&2
        exit 1
    fi
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
# Accepts an optional pre-computed fingerprint: converge.sh computes the post-converge state
# once (after the last deploy step) and passes it here, so the stamp records exactly what was
# just deployed instead of a second, independently-recomputed value that could differ if state
# drifted between the two calls (TOCTOU).
# When called with no argument, recomputes the state (back-compat for other callers).
write_convergence_state() {
    local state="${1:-}"
    mkdir -p "$(dirname "${CONVERGE_STAMP}")"
    if [[ -n "${state}" ]]; then
        printf '%s\n' "${state}" > "${CONVERGE_STAMP}"
    else
        compute_convergence_state > "${CONVERGE_STAMP}"
    fi
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
#   code-only  — ONLY git_head (field 0) differs; every tracked artifact (units, auth, voice,
#                image digests) is unchanged. The caller must resolve this via a paths git-diff
#                (see _code_change_is_inert): a docs/CI-only commit is inert (skip), while a
#                source change whose new units/image are not installed/pulled yet at gate time
#                must still run a full converge. Fail-safe: undecidable → structural.
#   structural — at least one structural field differs (git+artifact, units, voice, image digests)
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

    # code-only: git HEAD advanced but every tracked artifact is unchanged. Emitted so the
    # caller can cheaply skip a full converge for a docs/CI-only commit (paths git-diff),
    # while still converging when a source change's units/image aren't installed/pulled yet.
    if [ "${last_git}"     != "${cur_git}"     ] \
    && [ "${last_unit}"    =  "${cur_unit}"    ] \
    && [ "${last_auth}"    =  "${cur_auth}"    ] \
    && [ "${last_voice}"   =  "${cur_voice}"   ] \
    && [ "${last_img_svc}" =  "${cur_img_svc}" ] \
    && [ "${last_img_stg}" =  "${cur_img_stg}" ]; then
        echo "code-only"
        return 0
    fi

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

# Single source of truth for the non-runtime (inert) path allowlist — shared by
# _code_change_is_inert and _code_change_is_image_carried. Extracting it here is what
# enforces their lockstep (review #2144: parallel-path-drift — the list was authored
# twice with only a comment binding the copies).
#
# `*.md`/`*.txt` are inert anywhere in the tree: no runtime config is a bind-mounted,
# read-at-startup .md/.txt today (all mounted config is *.json/*.conf/*.yml/*.toml — grep
# `Volume=` in deploy/quadlet/ before ever adding one), and any .md/.txt baked into the image
# moves image digest fields 4/5, so factory-post-autoupdate's independent digest poll still
# converges the fleet even when this git-diff path skips. Adversarially reviewed (0 holes).
_path_is_inert() {
    case "${1}" in
        docs/*|tests/*|artifacts/*|.github/*) return 0 ;;
        *.md|*.txt|LICENSE|CHANGELOG|CHANGELOG.md|.gitignore|.editorconfig|.pre-commit-config.yaml) return 0 ;;
        *) return 1 ;;
    esac
}

# Both stamp git_heads must be real, resolvable commits BEFORE they are handed to
# git diff — a corrupted stamp value must not be parseable as a flag/pathspec or
# resolve to something unexpected (review #2144: stamp fields are trusted input).
# Shared by both code-only classifiers (same lockstep rationale as _path_is_inert).
_stamp_commits_resolvable() {
    [ -n "${1}" ] && [ "${1}" != "none" ] || return 1
    [ -n "${2}" ] && [ "${2}" != "none" ] || return 1
    (cd "${FACTORY_DIR}" \
        && git rev-parse --verify --quiet "${1}^{commit}" >/dev/null \
        && git rev-parse --verify --quiet "${2}^{commit}" >/dev/null) || return 1
}

# Decide whether a 'code-only' drift (git HEAD advanced, no tracked artifact changed) is INERT —
# i.e. the commit range touches only non-runtime files and needs no converge/restart.
#
#   _code_change_is_inert <last_fingerprint> <current_fingerprint>
#
# Returns 0 (inert → safe to skip) ONLY if EVERY file changed between the two stamps' git_head
# (field 0) matches the conservative non-runtime allowlist (_path_is_inert).
# Returns 1 (NOT inert → run a full converge) for any runtime-relevant path (src, packages, apps,
# deploy, tools, config, Dockerfile, lockfiles, …) AND for any undecidable case (a git_head is
# "none"/empty, or `git diff` fails because a commit is missing). This is a NEGATIVE allowlist:
# a mis-classification can only ever over-restart (current behaviour), never silently under-restart.
_code_change_is_inert() {
    local last_git cur_git changed p
    last_git=$(cut -d: -f1 <<< "${1}")
    cur_git=$(cut -d: -f1 <<< "${2}")

    # Both commits must be real and resolvable — else fail-safe to a full converge.
    _stamp_commits_resolvable "${last_git}" "${cur_git}" || return 1

    changed=$(cd "${FACTORY_DIR}" && git diff --name-only "${last_git}" "${cur_git}" 2>/dev/null) \
        || return 1
    # Empty diff = identical trees (e.g. a no-op/empty commit) → genuinely inert.
    [ -z "${changed}" ] && return 0

    while IFS= read -r p; do
        [ -z "${p}" ] && continue
        # a runtime-relevant path changed → NOT inert → full converge
        _path_is_inert "${p}" || return 1
    done <<< "${changed}"
    return 0
}

# Decide whether a 'code-only' drift is IMAGE-CARRIED — i.e. every changed file is either
# inert (same allowlist as _code_change_is_inert, kept in lockstep) or ships to the fleet
# exclusively inside the tracked images (src/, packages/, apps/dashboard/, brand/ — baked
# in by publish.yml, never bind-mounted from the checkout: every bind-mounted runtime
# config lives under deploy/, which is deliberately NOT in this allowlist; the %h/projects
# mounts in clipool/omp are live agent workspaces a restart cannot refresh further).
# Grep `Volume=` in deploy/quadlet/ before ever bind-mounting one of these dirs — a mounted
# src/packages/apps/brand path would break this classifier's core invariant.
#
#   _code_change_is_image_carried <last_fingerprint> <current_fingerprint>
#
# Returns 0 (image-carried → skip the pre-image restart) ONLY if EVERY changed path matches
# the inert ∪ image-carried allowlist. The restart is deferred, not lost: publish.yml builds
# the new image, then factory-post-autoupdate (*:2/5) detects the digest drift on fields 4/5,
# pulls, and runs the structural converge that actually carries the new code — INCLUDING the
# host steps (unit render, auth regen) that execute src/ code from the checkout at converge
# time; their effects now land at image-arrival instead of merge time (bounded by the digest
# loop). This skip REQUIRES post-autoupdate's unconditional change-gated converge: when
# podman-auto-update (*:4/5) wins the digest race it pulls (remote==local afterwards) without
# running any host step, and only the stale stamp fields 4/5 re-arm the converge. Restarting
# before the image exists deploys nothing: the fleet bounces on the OLD image, then bounces
# again when the image lands ("every code merge = 2 full-fleet restarts", audit 2026-07-01 §5.7).
#
# Returns 1 (NOT image-carried → full converge now) for any host-carried path (deploy/,
# tools/, scripts/, Dockerfile, docker/, pyproject.toml, uv.lock, Makefile, …) AND for any
# undecidable case. Same fail-safe direction as _code_change_is_inert: a mis-classification
# can only over-restart. If the image build fails (red staging CI → publish skipped), the
# fleet keeps the old image — the same end state today's pre-image restart produces, since
# that restart carries no new code either.
_code_change_is_image_carried() {
    local last_git cur_git changed p
    last_git=$(cut -d: -f1 <<< "${1}")
    cur_git=$(cut -d: -f1 <<< "${2}")

    # Both commits must be real and resolvable — else fail-safe to a full converge.
    _stamp_commits_resolvable "${last_git}" "${cur_git}" || return 1

    changed=$(cd "${FACTORY_DIR}" && git diff --name-only "${last_git}" "${cur_git}" 2>/dev/null) \
        || return 1
    # Empty diff = identical trees (e.g. a no-op/empty commit) → nothing to restart for.
    [ -z "${changed}" ] && return 0

    while IFS= read -r p; do
        [ -z "${p}" ] && continue
        case "${p}" in
            # image-carried: reaches the fleet only via factory:staging-svc / factory:staging.
            # apps/dashboard/ (not apps/*): only the dashboard is baked into an image —
            # apps/artifacts/ etc. ship in NO tracked image, so their digest re-arm never
            # fires; anything else under apps/ falls through to structural (fail-safe).
            src/*|packages/*|apps/dashboard/*|brand/*) continue ;;
        esac
        # not image-carried → inert (shared allowlist) or host-carried → full converge now
        _path_is_inert "${p}" || return 1
    done <<< "${changed}"
    return 0
}
