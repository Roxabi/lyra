#!/usr/bin/env bash
# check-acl-matrix-spec.sh — verify acl-matrix.json is in sync with the
# sentinel-bracketed table in artifacts/specs/706-per-role-nkeys-acls-spec.mdx.
#
# Strategy: render a full table from EFFECTIVE_JSON (acl-matrix.json after
# request_reply_flows inbox grants are applied) and diff it against the spec
# sentinel block. For each (subject, identity) cell, the value is PUB, SUB,
# PUB+SUB, or — based on whether the subject appears in the identity's
# effective publish/subscribe arrays.
#
# Subjects are auto-derived: union of publish[] + subscribe[] across all active
# identities, filtered to lyra.* and _inbox.*, sorted alphabetically.
#
# Exit 0  → no drift.
# Exit 1  → drift; unified diff to stdout.
#
# Dependencies: jq, awk, diff (standard on CI).

set -euo pipefail

UPDATE=false
[[ "${1:-}" == "--update" ]] && UPDATE=true

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JSON="${REPO_ROOT}/deploy/nats/acl-matrix.json"
SPEC="${REPO_ROOT}/artifacts/specs/706-per-role-nkeys-acls-spec.mdx"

[[ -f "$JSON" ]] || { echo "::error::acl-matrix.json not found: $JSON"; exit 1; }
[[ -f "$SPEC" ]] || { echo "::error::spec file not found: $SPEC"; exit 1; }

# Pre-compute effective ACL (static grants + derived from request_reply_flows).
# Guard: skip flows that reference unknown identities (prevents auto-vivification).
# Use |= unique after each += to prevent duplicate entries when the same identity
# appears as requester/responder in multiple flows.
EFFECTIVE_JSON=$(jq '
  reduce (.request_reply_flows[]?) as $flow (
    .;
    select(.identities[$flow.requester] != null and .identities[$flow.responder] != null) |
    .identities[$flow.requester].subscribe |= (. + ["_inbox.\($flow.requester).>"] | unique) |
    .identities[$flow.responder].publish   |= (. + ["_inbox.\($flow.requester).>"] | unique)
  )
' "$JSON")

# ---------------------------------------------------------------------------
# Render the table from EFFECTIVE_JSON in a single jq pass.
# Derives active identities + lyra.* / _inbox.* subjects from the same
# post-expansion source; computes all cells; outputs markdown directly.
# No bash string parsing of JSON data — all field splitting stays inside jq.
# ---------------------------------------------------------------------------
render_table() {
  jq -r '
    . as $eff |
    ([ .identities | to_entries[] | select(.value.status == "active") | .key ]) as $ids |
    (
      [
        .identities | to_entries[] |
        select(.value.status == "active") |
        .value | (.publish // []) + (.subscribe // [])
      ] |
      flatten | unique |
      map(select(startswith("lyra.") or startswith("_inbox.")))
    ) as $subjects |

    "| Subject |" + ($ids | map(" \(.) |") | join("")),
    "|---|" + ($ids | map(":-:|") | join("")),
    (
      $subjects[] |
      . as $subj |
      "| `\($subj)` |" + (
        [ $ids[] |
          . as $id |
          (($eff.identities[$id].publish  // []) | any(. == $subj)) as $pub |
          (($eff.identities[$id].subscribe // []) | any(. == $subj)) as $sub |
          if   $pub and $sub then " PUB+SUB |"
          elif $pub            then " PUB |"
          elif $sub            then " SUB |"
          else                      " — |"
          end
        ] | join("")
      )
    )
  ' <<< "$EFFECTIVE_JSON"
}

# ---------------------------------------------------------------------------
# Extract sentinel block from spec (without the marker lines themselves)
# ---------------------------------------------------------------------------
NATS_TMPDIR=$(mktemp -d)
trap 'rm -rf "$NATS_TMPDIR"' EXIT

SPEC_BLOCK="${NATS_TMPDIR}/spec_block.txt"
RENDERED="${NATS_TMPDIR}/rendered.txt"

awk '/<!-- acl-matrix:begin -->/{found=1; next} /<!-- acl-matrix:end -->/{found=0} found' \
  "$SPEC" > "$SPEC_BLOCK"

render_table > "$RENDERED"

if [ "${UPDATE}" = true ]; then
  if [ "${CI:-}" = "true" ]; then
    echo "::error::--update is a local-only flag and must not be passed in CI (changes would be lost at runner teardown)"
    exit 1
  fi
  grep -q '<!-- acl-matrix:begin -->' "$SPEC" && grep -q '<!-- acl-matrix:end -->' "$SPEC" \
    || { echo "::error::sentinel markers missing in $SPEC — cannot update safely"; exit 1; }
  awk '
    /<!-- acl-matrix:begin -->/ { print; found=1; next }
    /<!-- acl-matrix:end -->/ { found=0; while ((getline line < RENDERED) > 0) print line; print; next }
    !found { print }
  ' RENDERED="$RENDERED" "$SPEC" > "${NATS_TMPDIR}/spec_updated.txt"
  mv "${NATS_TMPDIR}/spec_updated.txt" "$SPEC"
  echo "Updated sentinel block in $SPEC"
  exit 0
fi

# ---------------------------------------------------------------------------
# Diff — exit 0 on match, 1 on drift
# ---------------------------------------------------------------------------
if diff -u "$SPEC_BLOCK" "$RENDERED"; then
  exit 0
else
  exit 1
fi
