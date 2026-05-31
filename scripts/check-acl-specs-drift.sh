#!/usr/bin/env bash
# check-acl-specs-drift.sh — verify that the generated ACL spec table and
# parity fixture are in sync with deploy/nats/acl-matrix.json.
#
# Strategy: render both views to temp files (via --output), then diff against
# the committed files.  The committed files are never written by this script —
# to fix drift run 'make nats-regen-specs' and commit the result.
#
# Exit-code contract (mirrors tools/check_architecture_snapshot.sh):
#   0 = clean (no drift)
#   1 = drift detected (committed files differ from freshly rendered output)
#   2 = generator crash (render script exited non-zero)
#
# Gated files:
#   artifacts/specs/706-per-role-nkeys-acls-spec.mdx
#   tests/scripts/fixtures/v3-current.json
#
# To fix drift locally: run 'make nats-regen-specs' and commit the result.
#
# Usage: bash scripts/check-acl-specs-drift.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

SPEC_COMMITTED="${REPO_ROOT}/artifacts/specs/706-per-role-nkeys-acls-spec.mdx"
FIXTURE_COMMITTED="${REPO_ROOT}/tests/scripts/fixtures/v3-current.json"

TMPDIR_GATE=$(mktemp -d)
trap 'rm -rf "$TMPDIR_GATE"' EXIT

SPEC_FRESH="${TMPDIR_GATE}/spec-fresh.mdx"
FIXTURE_FRESH="${TMPDIR_GATE}/v3-current-fresh.json"

# Render spec table to temp — exit 2 if generator crashes
cp "$SPEC_COMMITTED" "$SPEC_FRESH"
uv run --project "${REPO_ROOT}" python "${REPO_ROOT}/scripts/render_acl_spec.py" \
    --output "$SPEC_FRESH" || exit 2

# Render parity fixture to temp — exit 2 if generator crashes
uv run --project "${REPO_ROOT}" python "${REPO_ROOT}/scripts/render_acl_parity.py" \
    --output "$FIXTURE_FRESH" || exit 2

# Diff committed vs freshly rendered
drift=0
if ! diff -u "$SPEC_COMMITTED" "$SPEC_FRESH"; then
  drift=1
fi
if ! diff -u "$FIXTURE_COMMITTED" "$FIXTURE_FRESH"; then
  drift=1
fi

if [ "$drift" -ne 0 ]; then
  echo "::error::ACL spec/fixture views are stale. Run 'make nats-regen-specs' and commit the result." >&2
  exit 1
fi
