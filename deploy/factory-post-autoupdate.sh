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

# FACTORY_TRACKED_IMAGES sourced from deploy-common.sh (fields 5–6 of converge stamp).

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

local_repo_digests() {
    # All registry digests the local image is known by (index + per-arch).
    # Empty output when the image is absent (cold pull) → treated as drift.
    podman image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$1" 2>/dev/null \
        | sed 's/.*@//' || true
}

main() {
    local drifted=()

    for image in "${FACTORY_TRACKED_IMAGES[@]}"; do
        local remote_digest_val local_digests_val
        remote_digest_val=$(remote_digest "${image}")
        local_digests_val=$(local_repo_digests "${image}")

        # An image is unchanged iff the remote index digest appears (exact line
        # match) in the local RepoDigests set. Using RepoDigests rather than
        # .Digest fixes the false-drift bug (#1749): podman image inspect
        # .Digest returns the per-platform (amd64) digest while skopeo returns
        # the OCI index digest — they are always different on multi-arch images.
        # RepoDigests contains BOTH the index and per-arch digests so the index
        # digest from the remote will match here when the local image is current.
        if [ -n "${remote_digest_val}" ] && echo "${local_digests_val}" | grep -Fxq "${remote_digest_val}"; then
            echo "Image digest unchanged (${image})."
            echo "  remote: ${remote_digest_val}"
            echo "  local:  ${local_digests_val:-<not present>}"
        else
            echo "Image digest drift detected (${image}):"
            echo "  remote: ${remote_digest_val}"
            echo "  local:  ${local_digests_val:-<not present>}"
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

    # Image digests are fields 5–6 of the convergence fingerprint — converge's
    # change-gate detects structural drift after pull (no stamp deletion needed).
    echo "==> Running make converge..."
    make -C "${FACTORY_DIR}" converge
}

# Do NOT wrap main() in with_deploy_lock here. converge.sh already ends with
# `with_deploy_lock _do_converge`, so wrapping here too would cause the outer
# flock to hold the lock while calling make converge → converge.sh's inner
# flock -n fails → _do_converge silently exits 0 and no converge runs. (#1749)
# The pull + converge change-gate above are idempotent and safe to run unlocked;
# converge.sh's lock provides the necessary mutual exclusion.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
