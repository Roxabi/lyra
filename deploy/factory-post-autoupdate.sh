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

# FACTORY_TRACKED_IMAGES sourced from deploy-common.sh (fields 4–5 of converge stamp).

_refresh_fleet_digests() {
    bash "${FACTORY_DIR}/deploy/fleet-digest-poll.sh" || true
}

main() {
    local drifted=()

    for image in "${FACTORY_TRACKED_IMAGES[@]}"; do
        local remote_digest_val local_canonical_val
        remote_digest_val=$(factory_remote_index_digest "${image}" 3)
        local_canonical_val=$(factory_canonical_image_digest "${image}")

        # Unchanged when the canonical local digest matches the remote index digest.
        # factory_canonical_image_digest prefers the index digest when present in
        # RepoDigests — same rule as converge stamp fields 4–5 (#1749).
        if [ -n "${remote_digest_val}" ] && [ "${local_canonical_val}" != "none" ] \
            && [ "${local_canonical_val}" = "${remote_digest_val}" ]; then
            echo "Image digest unchanged (${image})."
            echo "  remote:   sha256:${remote_digest_val}"
            echo "  canonical: sha256:${local_canonical_val}"
        else
            echo "Image digest drift detected (${image}):"
            echo "  remote:    sha256:${remote_digest_val:-<unavailable>}"
            echo "  canonical: ${local_canonical_val}"
            drifted+=("${image}")
        fi
    done

    if [ "${#drifted[@]}" -eq 0 ]; then
        echo "All tracked image digests unchanged — nothing to do."
        _refresh_fleet_digests
        exit 0
    fi

    for image in "${drifted[@]}"; do
        echo "==> Pulling ${image}..."
        podman pull "${image}"
    done

    # Image digests are fields 4–5 of the convergence fingerprint — converge's
    # change-gate detects structural drift after pull (no stamp deletion needed).
    echo "==> Running make converge..."
    make -C "${FACTORY_DIR}" converge
    _refresh_fleet_digests
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