#!/usr/bin/env bash
# Regression test for deploy/factory-post-autoupdate.sh (bugs fixed in #1749).
#
# Tests three scenarios using real observed digests from M₁ (2026-06-05):
#   staging-svc index digest (skopeo): sha256:4f9b3264…
#   staging-svc per-arch digest (podman .Digest): sha256:6a7d28bc…
#   RepoDigests contains BOTH digests.
#
# Case A — remote index digest ∈ local RepoDigests → UNCHANGED / no converge.
# Case B — remote index digest ∉ local RepoDigests (new push) → DRIFT / converge triggered.
# Case C — per-arch .Digest matches remote but index digest ∈ RepoDigests → UNCHANGED
#          (core regression guard: old code compared .Digest↔index → always mismatch;
#          new code uses membership in RepoDigests → correctly unchanged).
#
# Usage: bash tests/deploy/test_post_autoupdate.sh
set -euo pipefail

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PASS=0
FAIL=0

pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1: $2"; FAIL=$((FAIL + 1)); exit 1; }

assert_contains() {
    local label="$1" haystack="$2" needle="$3"
    if echo "$haystack" | grep -qF "$needle"; then
        pass "$label"
    else
        fail "$label" "expected to find '$needle' in output; got: $haystack"
    fi
}

assert_not_contains() {
    local label="$1" haystack="$2" needle="$3"
    if echo "$haystack" | grep -qF "$needle"; then
        fail "$label" "expected NOT to find '$needle' in output; got: $haystack"
    else
        pass "$label"
    fi
}

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
HOOK_SRC="$REPO_ROOT/deploy/factory-post-autoupdate.sh"

if [ ! -f "$HOOK_SRC" ]; then
    echo "ERROR: script not found at $HOOK_SRC" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Temp dir + cleanup
# ---------------------------------------------------------------------------

TMPDIR_WORK="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_WORK"' EXIT

# ---------------------------------------------------------------------------
# Fixtures (real M₁ digests, 2026-06-05)
# ---------------------------------------------------------------------------

INDEX_DIGEST_SVC="sha256:4f9b3264aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa0001"
ARCH_DIGEST_SVC="sha256:6a7d28bcaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa0001"
INDEX_DIGEST_STG="sha256:4f9b3264aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa0002"
ARCH_DIGEST_STG="sha256:6a7d28bcaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa0002"
NEW_INDEX_DIGEST_SVC="sha256:deadbeefaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa0001"

# ---------------------------------------------------------------------------
# Build a test-local copy of the hook under a temp deploy/ tree so that
# `dirname "$0"` resolves correctly when the hook sources deploy-common.sh.
# The hook file uses:  source "$(dirname "$0")/lib/deploy-common.sh"
# When sourced by our wrapper, $0 is the wrapper — so we COPY (not symlink)
# the hook into the temp deploy dir and place our stub lib there too.
# ---------------------------------------------------------------------------

FAKE_DEPLOY_DIR="$TMPDIR_WORK/deploy"
mkdir -p "$FAKE_DEPLOY_DIR/lib"

# Stub deploy-common.sh — only the surface the hook uses
cat > "$FAKE_DEPLOY_DIR/lib/deploy-common.sh" <<EOF
set -euo pipefail
FACTORY_DIR="$TMPDIR_WORK/factory"
CONVERGE_STAMP="$TMPDIR_WORK/converge-stamp"
mkdir -p "\$FACTORY_DIR"
# No-op lock wrapper for tests — converge.sh owns the real lock in production
with_deploy_lock() { "\$@"; }
EOF

# Copy (not symlink) the hook so dirname "$0" → FAKE_DEPLOY_DIR
cp "$HOOK_SRC" "$FAKE_DEPLOY_DIR/factory-post-autoupdate.sh"

# ---------------------------------------------------------------------------
# Helper: run main() by executing the hook from within $FAKE_DEPLOY_DIR.
#
# The hook sources "$(dirname "$0")/lib/deploy-common.sh". "$0" is the
# *executing* script's path. By placing the wrapper inside $FAKE_DEPLOY_DIR
# and calling the hook via `source ./factory-post-autoupdate.sh`, dirname "$0"
# resolves to $FAKE_DEPLOY_DIR where our stub lib/ lives.
# ---------------------------------------------------------------------------

run_hook_main() {
    local shims_file="$1"
    # Wrapper lives INSIDE FAKE_DEPLOY_DIR so dirname "$0" → FAKE_DEPLOY_DIR
    local wrapper="$FAKE_DEPLOY_DIR/run_main_$$.sh"
    cat > "$wrapper" <<WRAPPER
#!/usr/bin/env bash
set -euo pipefail
source "${shims_file}"
source "\$(dirname "\$0")/factory-post-autoupdate.sh"
main
WRAPPER
    bash "$wrapper"
    rm -f "$wrapper"
}

# ---------------------------------------------------------------------------
# Case A — remote index digest ∈ RepoDigests → UNCHANGED, no make converge
# ---------------------------------------------------------------------------

SHIMS_A="$TMPDIR_WORK/shims_a.sh"
cat > "$SHIMS_A" <<EOF
skopeo() {
    # Called as: skopeo inspect "docker://<image>"
    # \$1=inspect \$2=docker://<image>
    local img="\$2"
    case "\$img" in
        *staging-svc*) printf '{"Digest":"%s"}' "${INDEX_DIGEST_SVC}" ;;
        *staging*)     printf '{"Digest":"%s"}' "${INDEX_DIGEST_STG}" ;;
    esac
}
podman() {
    if [ "\$1" = "image" ] && [ "\$2" = "inspect" ]; then
        local img="\${*: -1}"
        case "\$img" in
            *staging-svc*)
                printf '%s\n' \
                    "ghcr.io/roxabi/factory@${INDEX_DIGEST_SVC}" \
                    "ghcr.io/roxabi/factory@${ARCH_DIGEST_SVC}"
                ;;
            *staging*)
                printf '%s\n' \
                    "ghcr.io/roxabi/factory@${INDEX_DIGEST_STG}" \
                    "ghcr.io/roxabi/factory@${ARCH_DIGEST_STG}"
                ;;
        esac
        return 0
    fi
    return 0
}
jq() {
    if [ "\$1" = "-r" ] && [ "\$2" = ".Digest" ]; then
        grep -o '"Digest":"[^"]*"' | sed 's/"Digest":"//;s/"//'
    else
        command jq "\$@"
    fi
}
make() { echo "[ERROR] make called unexpectedly in case A" >&2; exit 1; }
export -f skopeo podman jq make
EOF

OUTPUT_A=$(run_hook_main "$SHIMS_A" 2>&1 || true)

assert_contains     "A: staging-svc unchanged"    "$OUTPUT_A" "Image digest unchanged (ghcr.io/roxabi/factory:staging-svc)"
assert_contains     "A: staging unchanged"         "$OUTPUT_A" "Image digest unchanged (ghcr.io/roxabi/factory:staging)"
assert_contains     "A: nothing to do"             "$OUTPUT_A" "All tracked image digests unchanged — nothing to do."
assert_not_contains "A: no drift"                  "$OUTPUT_A" "drift detected"
assert_not_contains "A: no make converge"          "$OUTPUT_A" "Running make converge"

# ---------------------------------------------------------------------------
# Case B — remote index digest ∉ RepoDigests (new push) → DRIFT, converge triggered
# ---------------------------------------------------------------------------

MAKE_CALLED_FILE="$TMPDIR_WORK/make_called_b"
SHIMS_B="$TMPDIR_WORK/shims_b.sh"
cat > "$SHIMS_B" <<EOF
skopeo() {
    # Called as: skopeo inspect "docker://<image>"
    # \$1=inspect \$2=docker://<image>
    local img="\$2"
    case "\$img" in
        *staging-svc*) printf '{"Digest":"%s"}' "${NEW_INDEX_DIGEST_SVC}" ;;
        *staging*)     printf '{"Digest":"%s"}' "${INDEX_DIGEST_STG}" ;;
    esac
}
podman() {
    if [ "\$1" = "image" ] && [ "\$2" = "inspect" ]; then
        local img="\${*: -1}"
        case "\$img" in
            *staging-svc*)
                # Old digests — new push not yet pulled locally
                printf '%s\n' \
                    "ghcr.io/roxabi/factory@${INDEX_DIGEST_SVC}" \
                    "ghcr.io/roxabi/factory@${ARCH_DIGEST_SVC}"
                ;;
            *staging*)
                printf '%s\n' \
                    "ghcr.io/roxabi/factory@${INDEX_DIGEST_STG}" \
                    "ghcr.io/roxabi/factory@${ARCH_DIGEST_STG}"
                ;;
        esac
        return 0
    fi
    if [ "\$1" = "pull" ]; then
        return 0
    fi
    return 0
}
jq() {
    if [ "\$1" = "-r" ] && [ "\$2" = ".Digest" ]; then
        grep -o '"Digest":"[^"]*"' | sed 's/"Digest":"//;s/"//'
    else
        command jq "\$@"
    fi
}
make() {
    touch "${MAKE_CALLED_FILE}"
    echo "make converge called"
}
export -f skopeo podman jq make
EOF

OUTPUT_B=$(run_hook_main "$SHIMS_B" 2>&1 || true)

assert_contains     "B: staging-svc drift detected" "$OUTPUT_B" "Image digest drift detected (ghcr.io/roxabi/factory:staging-svc)"
assert_contains     "B: staging unchanged"           "$OUTPUT_B" "Image digest unchanged (ghcr.io/roxabi/factory:staging)"
assert_contains     "B: make converge called"        "$OUTPUT_B" "make converge called"
[ -f "$MAKE_CALLED_FILE" ] \
    && pass "B: make sentinel file created" \
    || fail "B: make sentinel file" "not created — make was not called"

# ---------------------------------------------------------------------------
# Case C — per-arch .Digest differs from remote index digest, but index digest
#           IS in RepoDigests → UNCHANGED (core regression guard for #1749).
#
#           Old code:  local_digest() returned podman .Digest (per-arch);
#                      compared ARCH_DIGEST vs INDEX_DIGEST → always mismatch → DRIFT (BUG).
#           New code:  local_repo_digests() returns all RepoDigests;
#                      grep -Fxq INDEX_DIGEST in list that contains it → UNCHANGED (CORRECT).
# ---------------------------------------------------------------------------

SHIMS_C="$TMPDIR_WORK/shims_c.sh"
cat > "$SHIMS_C" <<EOF
skopeo() {
    # Called as: skopeo inspect "docker://<image>"
    # \$1=inspect \$2=docker://<image>
    local img="\$2"
    case "\$img" in
        *staging-svc*) printf '{"Digest":"%s"}' "${INDEX_DIGEST_SVC}" ;;
        *staging*)     printf '{"Digest":"%s"}' "${INDEX_DIGEST_STG}" ;;
    esac
}
podman() {
    if [ "\$1" = "image" ] && [ "\$2" = "inspect" ]; then
        local img="\${*: -1}"
        # RepoDigests has BOTH index and per-arch digests (realistic multi-arch state).
        # Crucially, INDEX_DIGEST != ARCH_DIGEST — old code always reported drift here.
        case "\$img" in
            *staging-svc*)
                printf '%s\n' \
                    "ghcr.io/roxabi/factory@${INDEX_DIGEST_SVC}" \
                    "ghcr.io/roxabi/factory@${ARCH_DIGEST_SVC}"
                ;;
            *staging*)
                printf '%s\n' \
                    "ghcr.io/roxabi/factory@${INDEX_DIGEST_STG}" \
                    "ghcr.io/roxabi/factory@${ARCH_DIGEST_STG}"
                ;;
        esac
        return 0
    fi
    return 0
}
jq() {
    if [ "\$1" = "-r" ] && [ "\$2" = ".Digest" ]; then
        grep -o '"Digest":"[^"]*"' | sed 's/"Digest":"//;s/"//'
    else
        command jq "\$@"
    fi
}
make() { echo "[ERROR] make called unexpectedly in case C" >&2; exit 1; }
export -f skopeo podman jq make
EOF

OUTPUT_C=$(run_hook_main "$SHIMS_C" 2>&1 || true)

assert_contains     "C: unchanged (index ∈ RepoDigests)"   "$OUTPUT_C" "Image digest unchanged (ghcr.io/roxabi/factory:staging-svc)"
assert_not_contains "C: no false drift"                    "$OUTPUT_C" "drift detected"
assert_not_contains "C: no make converge"                  "$OUTPUT_C" "Running make converge"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
