#!/usr/bin/env bash
# deploy/factory-post-autoupdate.sh — poll image digests and trigger converge on change
#
# Triggered by factory-post-autoupdate.timer (5 min). Idempotent: if every tracked
# image digest matches local, exits 0. If ANY image digest drifted, pulls the
# drifted image(s) and runs a single full converge.
#
# Tracks both runtime image tags:
#   :staging-svc  — service runtime (hub, adapters, turn-writer, blobstore)
#   :staging      — agent runtime (clipool, gh-helper)
# A change to EITHER must trigger converge: a staging merge can rebuild only one
# tag while still carrying an auth.conf/ACL change that NATS needs a restart
# (not HUP) to pick up. See #1733.

set -euo pipefail

source "$(dirname "$0")/lib/deploy-common.sh"

IMAGES=(
    "ghcr.io/roxabi/factory:staging-svc"
    "ghcr.io/roxabi/factory:staging"
)

# ── Digest comparison ────────────────────────────────────────────────────────

remote_digest() {
    local image="$1" attempt delay out
    for attempt in 1 2 3; do
        if out=$(skopeo inspect "docker://${image}" 2>/dev/null); then
            printf '%s' "$out" | jq -r '.Digest'
            return 0
        fi
        delay=$(( attempt * 2 ))
        echo "skopeo inspect failed (attempt ${attempt}/3), retrying in ${delay}s..." >&2
        sleep "${delay}"
    done
    echo "skopeo inspect failed after 3 attempts for ${image}" >&2
    return 1
}

local_digest() {
    # image inspect returns exactly one digest (or errors when absent) — no
    # multi-line ambiguity from `podman images` listing dangling layers.
    podman image inspect --format '{{.Digest}}' "$1" 2>/dev/null || true
}

main() {
    local drifted=()

    for image in "${IMAGES[@]}"; do
        local remote_digest_val local_digest_val
        remote_digest_val=$(remote_digest "${image}")
        local_digest_val=$(local_digest "${image}")

        if [ -n "${local_digest_val}" ] && [ "${remote_digest_val}" = "${local_digest_val}" ]; then
            echo "Image digest unchanged (${image})."
        else
            echo "Image digest drift detected (${image}):"
            echo "  remote: ${remote_digest_val}"
            echo "  local:  ${local_digest_val:-<not present>}"
            drifted+=("${image}")
        fi
    done

    if [ "${#drifted[@]}" -eq 0 ]; then
        echo "All tracked image digests unchanged — nothing to do."
        exit 0
    fi

    for image in "${drifted[@]}"; do
        echo "==> Pulling ${image}..."
        podman pull "${image}"
    done

    # Converge stamp does not include image digest, so invalidate it
    # to force a full converge (restarts, auth.conf refresh, etc.).
    rm -f "${CONVERGE_STAMP}"

    echo "==> Running make converge..."
    make -C "${FACTORY_DIR}" converge
}

with_deploy_lock main
