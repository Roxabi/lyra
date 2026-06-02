#!/usr/bin/env bash
# deploy/factory-post-autoupdate.sh — poll image digest and trigger converge on change
#
# Triggered by factory-post-autoupdate.timer (5 min). Idempotent: if digests match,
# exits 0. On digest change, pulls the new image and runs the full converge.

set -euo pipefail

source "$(dirname "$0")/lib/deploy-common.sh"

IMAGE="ghcr.io/roxabi/factory:staging-svc"

# ── Digest comparison ────────────────────────────────────────────────────────

remote_digest() {
    skopeo inspect "docker://${IMAGE}" | jq -r '.Digest'
}

local_digest() {
    podman images --format '{{.Digest}}' "${IMAGE}" 2>/dev/null || true
}

main() {
    local remote_digest_val local_digest_val

    remote_digest_val=$(remote_digest)
    local_digest_val=$(local_digest)

    if [ -n "${local_digest_val}" ] && [ "${remote_digest_val}" = "${local_digest_val}" ]; then
        echo "Image digest unchanged (${IMAGE}) — nothing to do."
        exit 0
    fi

    echo "Image digest drift detected:"
    echo "  remote: ${remote_digest_val}"
    echo "  local:  ${local_digest_val:-<not present>}"

    echo "==> Pulling ${IMAGE}..."
    podman pull "${IMAGE}"

    # Converge stamp does not include image digest, so invalidate it
    # to force a full converge (restarts, auth.conf refresh, etc.).
    rm -f "${CONVERGE_STAMP}"

    echo "==> Running make converge..."
    make -C "${LYRA_DIR}" converge
}

with_deploy_lock main
