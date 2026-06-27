#!/usr/bin/env bash
# Tests factory_canonical_image_digest — prefers skopeo index digest in RepoDigests.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEPLOY_COMMON="$REPO_ROOT/deploy/lib/deploy-common.sh"

TMPDIR_WORK="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_WORK"' EXIT

export HOME="$TMPDIR_WORK/home"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
mkdir -p "$HOME/projects/roxabi-factory" "$HOME/.roxabi/factory/nkeys"

INDEX="4f9b3264aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa0001"
ARCH="6a7d28bcaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa0001"

skopeo() {
    printf '{"Digest":"sha256:%s"}' "$INDEX"
}

podman() {
    if [ "$1" = "image" ] && [ "$2" = "inspect" ]; then
        printf '%s\n' \
            "ghcr.io/roxabi/factory@sha256:${INDEX}" \
            "ghcr.io/roxabi/factory@sha256:${ARCH}"
        return 0
    fi
    return 0
}

export -f skopeo podman

source "$DEPLOY_COMMON"

result=$(factory_canonical_image_digest "ghcr.io/roxabi/factory:staging-svc")
if [ "$result" = "$INDEX" ]; then
    echo "[PASS] canonical digest prefers index over first RepoDigest"
else
    echo "[FAIL] expected index digest ${INDEX}, got ${result}" >&2
    exit 1
fi

echo "All canonical digest tests passed."