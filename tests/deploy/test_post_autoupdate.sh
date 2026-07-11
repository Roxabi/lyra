#!/usr/bin/env bash
# Regression test for deploy/factory-post-autoupdate.sh (bugs fixed in #1749).
#
# Tests three scenarios using real observed digests from M₁ (2026-06-05):
#   staging-svc index digest (skopeo): sha256:4f9b3264…
#   staging-svc per-arch digest (podman .Digest): sha256:6a7d28bc…
#   RepoDigests contains BOTH digests.
#
# Case A — remote index digest ∈ local RepoDigests → UNCHANGED / no pull, but the
#          change-gated `make converge` still runs (podman-wins race self-heal — a
#          podman-auto-update pull leaves remote==local with a stale stamp, so a
#          drift-gated converge would be suppressed forever; converge no-ops when
#          the stamp matches).
# Case B — remote index digest ∉ local RepoDigests (new push) → DRIFT / pull + converge.
# Case C — per-arch .Digest matches remote but index digest ∈ RepoDigests → UNCHANGED
#          (core regression guard: old code compared .Digest↔index → always mismatch;
#          new code uses membership in RepoDigests → correctly no false drift/pull).
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

# Match full log lines — avoid :staging matching inside :staging-svc.
assert_line() {
    local label="$1" haystack="$2" needle="$3"
    if echo "$haystack" | grep -qxF "$needle"; then
        pass "$label"
    else
        fail "$label" "expected line '$needle'; output:\n${haystack}"
    fi
}

assert_not_line() {
    local label="$1" haystack="$2" needle="$3"
    if echo "$haystack" | grep -qxF "$needle"; then
        fail "$label" "expected NOT to see line '$needle'; output:\n${haystack}"
    else
        pass "$label"
    fi
}

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

_unchanged_line() {
    local image="$1"
    echo "Image digest unchanged (${image})."
}

_drift_line_prefix() {
    local image="$1"
    echo "Image digest drift detected (${image}):"
}

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
HOOK_SRC="$REPO_ROOT/deploy/factory-post-autoupdate.sh"
IMAGE_SVC="ghcr.io/roxabi/factory:staging-svc"
IMAGE_STG="ghcr.io/roxabi/factory:staging"

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
# FACTORY_DIR points at the real checkout so auxiliary deploy scripts
# (fleet-digest-poll.sh) resolve; only podman/skopeo/make/uv are shimmed.
# ---------------------------------------------------------------------------

FAKE_DEPLOY_DIR="$TMPDIR_WORK/deploy"
mkdir -p "$FAKE_DEPLOY_DIR/lib"

cat > "$FAKE_DEPLOY_DIR/lib/deploy-common.sh" <<EOF
set -euo pipefail
export HOME="$TMPDIR_WORK/home"
export FACTORY_DIR="$REPO_ROOT"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
mkdir -p "\$HOME/.roxabi/factory/nkeys"
source "$REPO_ROOT/deploy/lib/deploy-common.sh"
with_deploy_lock() { "\$@"; }
EOF

cp "$HOOK_SRC" "$FAKE_DEPLOY_DIR/factory-post-autoupdate.sh"

write_shims() {
    local dest="$1"
    local remote_svc="$2"
    local remote_stg="$3"
    local pull_ok="${4:-false}"
    cat > "$dest" <<EOF
_image_ref_tag() {
    local ref="\$1"
    ref="\${ref#docker://}"
    case "\${ref##*:}" in
        staging-svc) echo "staging-svc" ;;
        staging)     echo "staging" ;;
        *)           echo "other" ;;
    esac
}
skopeo() {
    local img="\$2"
    case "\$(_image_ref_tag "\$img")" in
        staging-svc) printf '{"Digest":"%s"}' "${remote_svc}" ;;
        staging)     printf '{"Digest":"%s"}' "${remote_stg}" ;;
        *)           return 1 ;;
    esac
}
podman() {
    if [ "\$1" = "image" ] && [ "\$2" = "inspect" ]; then
        local img="\${*: -1}"
        case "\$(_image_ref_tag "\$img")" in
            staging-svc)
                printf '%s\n' \
                    "ghcr.io/roxabi/factory@${INDEX_DIGEST_SVC}" \
                    "ghcr.io/roxabi/factory@${ARCH_DIGEST_SVC}"
                ;;
            staging)
                printf '%s\n' \
                    "ghcr.io/roxabi/factory@${INDEX_DIGEST_STG}" \
                    "ghcr.io/roxabi/factory@${ARCH_DIGEST_STG}"
                ;;
            *) return 1 ;;
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
uv() {
    # fleet-digest-poll.sh is invoked at end of main(); keep the contract test hermetic.
    return 0
}
export -f skopeo podman jq uv _image_ref_tag
EOF
}

run_hook_main() {
    local shims_file="$1"
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
# Case A — remote index digest ∈ RepoDigests → UNCHANGED: no pull, but the
#          change-gated make converge still runs (podman-wins self-heal)
# ---------------------------------------------------------------------------

MAKE_CALLED_FILE_A="$TMPDIR_WORK/make_called_a"
SHIMS_A="$TMPDIR_WORK/shims_a.sh"
write_shims "$SHIMS_A" "$INDEX_DIGEST_SVC" "$INDEX_DIGEST_STG"
cat >> "$SHIMS_A" <<EOF
make() {
    touch "${MAKE_CALLED_FILE_A}"
    echo "make converge called"
}
export -f make
EOF

OUTPUT_A=$(run_hook_main "$SHIMS_A" 2>&1 || true)

assert_line         "A: staging-svc unchanged"    "$OUTPUT_A" "$(_unchanged_line "$IMAGE_SVC")"
assert_line         "A: staging unchanged"         "$OUTPUT_A" "$(_unchanged_line "$IMAGE_STG")"
assert_contains     "A: no pull needed"            "$OUTPUT_A" "All tracked image digests unchanged — no pull needed."
assert_not_contains "A: no drift"                  "$OUTPUT_A" "drift detected"
assert_not_contains "A: no pull"                   "$OUTPUT_A" "==> Pulling"
assert_contains     "A: change-gated converge"     "$OUTPUT_A" "make converge called"
[ -f "$MAKE_CALLED_FILE_A" ] \
    && pass "A: make sentinel file created" \
    || fail "A: make sentinel file" "not created — the podman-wins self-heal converge was not called"

# ---------------------------------------------------------------------------
# Case B — remote index digest ∉ RepoDigests (new push) → DRIFT, converge triggered
# ---------------------------------------------------------------------------

MAKE_CALLED_FILE_B="$TMPDIR_WORK/make_called_b"
SHIMS_B="$TMPDIR_WORK/shims_b.sh"
write_shims "$SHIMS_B" "$NEW_INDEX_DIGEST_SVC" "$INDEX_DIGEST_STG" true
cat >> "$SHIMS_B" <<EOF
make() {
    touch "${MAKE_CALLED_FILE_B}"
    echo "make converge called"
}
export -f make
EOF

OUTPUT_B=$(run_hook_main "$SHIMS_B" 2>&1 || true)

assert_line         "B: staging-svc drift detected" "$OUTPUT_B" "$(_drift_line_prefix "$IMAGE_SVC")"
assert_line         "B: staging unchanged"           "$OUTPUT_B" "$(_unchanged_line "$IMAGE_STG")"
assert_contains     "B: make converge called"        "$OUTPUT_B" "make converge called"
[ -f "$MAKE_CALLED_FILE_B" ] \
    && pass "B: make sentinel file created" \
    || fail "B: make sentinel file" "not created — make was not called"

# ---------------------------------------------------------------------------
# Case C — per-arch .Digest differs from remote index digest, but index digest
#           IS in RepoDigests → UNCHANGED (core regression guard for #1749).
# ---------------------------------------------------------------------------

SHIMS_C="$TMPDIR_WORK/shims_c.sh"
write_shims "$SHIMS_C" "$INDEX_DIGEST_SVC" "$INDEX_DIGEST_STG"
cat >> "$SHIMS_C" <<EOF
make() { echo "make converge called"; }
export -f make
EOF

OUTPUT_C=$(run_hook_main "$SHIMS_C" 2>&1 || true)

assert_line         "C: unchanged (index ∈ RepoDigests)"   "$OUTPUT_C" "$(_unchanged_line "$IMAGE_SVC")"
assert_not_contains "C: no false drift"                    "$OUTPUT_C" "drift detected"
assert_not_contains "C: no false pull"                     "$OUTPUT_C" "==> Pulling"
assert_contains     "C: change-gated converge"             "$OUTPUT_C" "make converge called"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
