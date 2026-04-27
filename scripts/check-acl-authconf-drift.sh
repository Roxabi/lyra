#!/usr/bin/env bash
# check-acl-authconf-drift.sh — verify deploy/nats/auth.conf is in sync with
# deploy/nats/acl-matrix.json.
#
# Strategy: regenerate auth.conf template from acl-matrix.json using
# gen-nkeys.sh --template-only, then normalize both files (strip comments,
# normalize nkey values to placeholder) and diff.
#
# Exit 0 → no drift.
# Exit 1 → drift; unified diff to stdout.
#
# Dependencies: jq, awk, sed, diff (standard on CI).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AUTH_CONF="${REPO_ROOT}/deploy/nats/auth.conf"
ACL_MATRIX="${REPO_ROOT}/deploy/nats/acl-matrix.json"
GEN_NKEYS="${REPO_ROOT}/deploy/nats/gen-nkeys.sh"

# Ensure required files exist
for f in "$AUTH_CONF" "$ACL_MATRIX" "$GEN_NKEYS"; do
  [ -f "$f" ] || { echo "::error::Missing file: $f" >&2; exit 1; }
done

# Create temp directory for normalized outputs
NATS_TMPDIR=$(mktemp -d)
trap 'rm -rf "$NATS_TMPDIR"' EXIT

COMMITTED_NORM="${NATS_TMPDIR}/committed_norm.txt"
GENERATED_NORM="${NATS_TMPDIR}/generated_norm.txt"

# ---------------------------------------------------------------------------
# normalize_authconf: strip comments, normalize nkey values, normalize whitespace
# Input: auth.conf content on stdin
# Output: normalized content on stdout
# ---------------------------------------------------------------------------
normalize_authconf() {
  # 1. Strip comment lines (lines starting with #, ignoring leading whitespace)
  # 2. Strip trailing inline comments (safe: NATS conf quoted strings don't contain #)
  # 3. Normalize nkey values: replace any nkey value with "NKEY_PLACEHOLDER"
  # 4. Normalize whitespace: squeeze multiple spaces, trim trailing
  sed -E \
    -e '/^[[:space:]]*#/d' \
    -e 's/[[:space:]]*#.*$//' \
    -e 's/nkey: "[^"]+"/nkey: "NKEY_PLACEHOLDER"/g' \
    -e 's/[[:space:]]+/ /g' \
    -e 's/[[:space:]]+$//' \
    | grep -v '^$'
}

# Generate expected auth.conf from acl-matrix.json
bash "$GEN_NKEYS" --template-only > "${NATS_TMPDIR}/generated_auth.conf"

# Normalize both files
normalize_authconf < "$AUTH_CONF" > "$COMMITTED_NORM"
normalize_authconf < "${NATS_TMPDIR}/generated_auth.conf" > "$GENERATED_NORM"

# Diff — explicit capture makes intent clear under set -euo pipefail
diff_out=$(diff -u "$COMMITTED_NORM" "$GENERATED_NORM" || true)
if [ -z "$diff_out" ]; then
  echo "✓ auth.conf is in sync with acl-matrix.json"
  exit 0
else
  echo "$diff_out"
  echo "::error::auth.conf is out of sync with acl-matrix.json — run 'bash deploy/nats/gen-nkeys.sh --template-only > deploy/nats/auth.conf' and commit the result" >&2
  exit 1
fi
