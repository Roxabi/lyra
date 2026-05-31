#!/usr/bin/env bash
# deploy/lyra-post-autoupdate.sh — poll image digest and trigger converge on change
#
# Triggered by lyra-post-autoupdate.timer (5 min). Idempotent: if digests match,
# exits 0. On digest change, pulls the new image and runs the full converge.

set -euo pipefail

source "$(dirname "$0")/lib/deploy-common.sh"

IMAGE="ghcr.io/roxabi/lyra:staging-svc"

# ── Digest comparison ────────────────────────────────────────────────────────

remote_digest() {
    skopeo inspect "docker://${IMAGE}" | jq -r '.Digest'
}

local_digest() {
    podman images --format '{{.Digest}}' "${IMAGE}" 2>/dev/null || true
}

main() {
    local remote local

    remote=$(remote_digest)
    local=$(local_digest)

    if [ -n "${local}" ] && [ "${remote}" = "${local}" ]; then
        echo "Image digest unchanged (${IMAGE}) — nothing to do."
        exit 0
    fi

    echo "Image digest drift detected:"
    echo "  remote: ${remote}"
    echo "  local:  ${local:-<not present>}"

    echo "==> Pulling ${IMAGE}..."
    podman pull "${IMAGE}"

    # Converge stamp does not include image digest, so invalidate it
    # to force a full converge (restarts, auth.conf refresh, etc.).
    rm -f "${CONVERGE_STAMP}"

    echo "==> Running make converge..."
    make -C "${LYRA_DIR}" converge
}

with_deploy_lock main
