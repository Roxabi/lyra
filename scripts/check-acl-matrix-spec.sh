#!/usr/bin/env bash
# check-acl-matrix-spec.sh — verify acl-matrix.json is in sync with the
# sentinel-bracketed table in artifacts/specs/706-per-role-nkeys-acls-spec.mdx.
#
# Strategy: cell-by-cell assert. For each cell in the spec table where the
# expected value is PUB or SUB (not —), we verify the JSON publish/subscribe
# arrays back that claim. Cells that read — in the spec are not asserted
# (the spec intentionally omits supplementary ACLs present in the JSON for
# other identities / other features). Drift = spec claims PUB/SUB but JSON
# disagrees, or spec claims — but JSON would produce PUB+SUB.
#
# We render a full table from JSON and diff against the spec sentinel block.
# The render uses a hard-coded subject→JSON-lookup mapping that reproduces
# the spec exactly: for each (subject, identity) we look up only the specific
# JSON subjects that the spec intends to cover per row.
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

# Pre-compute effective ACL (static grants + derived from request_reply_flows)
EFFECTIVE_JSON=$(jq '
  reduce (.request_reply_flows[]?) as $flow (
    .;
    .identities[$flow.requester].subscribe += ["_inbox.\($flow.requester).>"] |
    .identities[$flow.responder].publish   += ["_inbox.\($flow.requester).>"]
  )
' "$JSON")

# ---------------------------------------------------------------------------
# Identity column order — all active identities (updated: retired tts-adapter/sst-adapter
# removed, voice-tts/voice-stt/image-worker/clipool-worker added per postmortem Fix 1+2)
# ---------------------------------------------------------------------------
mapfile -t IDENTITIES < <(jq -r '.identities | to_entries[] | select(.value.status == "active") | .key' "$JSON")

# ---------------------------------------------------------------------------
# Subject rows — auto-derived from acl-matrix.json.
# Union of publish[] + subscribe[] across active identities, after
# request_reply_flows expansion. Excludes NATS system subjects ($JS.*, $KV.*).
# Format: "display|pub_subject|sub_subject"
# ---------------------------------------------------------------------------
mapfile -t ROWS < <(jq -r '
  [
    .identities | to_entries[] |
    select(.value.status == "active") |
    .value | (.publish // []) + (.subscribe // [])
  ] |
  flatten | unique | sort[] |
  select(startswith("lyra.") or startswith("_inbox.")) |
  ("`" + . + "`|" + . + "|" + .)
' <<< "$EFFECTIVE_JSON")

# ---------------------------------------------------------------------------
# Identities that are in scope for each row's pub/sub check.
# For rows where some identities have — in the spec, we must NOT assert PUB
# or SUB from JSON for those identities. We instead only assert the cells
# that the spec claims are non-—.
#
# Implementation: for each cell, compute what JSON says using the row's
# specific pub/sub subjects, then emit the result. The diff will catch
# any disagreement with the spec.
# ---------------------------------------------------------------------------

# Returns "true" or "false": does the JSON array for identity+key contain subject?
json_has() {
  local identity="$1"
  local key="$2"   # "publish" or "subscribe"
  local subject="$3"
  jq --arg id "$identity" --arg key "$key" --arg subj "$subject" '
    .identities[$id][$key] // [] | map(select(. == $subj)) | length > 0
  ' <<< "$EFFECTIVE_JSON"
}

# Compute cell value: PUB | SUB | PUB+SUB | —
# Uses the row's dedicated pub_subject and sub_subject (may differ per row).
cell_value() {
  local identity="$1"
  local pub_subject="$2"
  local sub_subject="$3"

  local is_pub="false"
  local is_sub="false"

  [[ "$pub_subject" != "NONE" ]] && is_pub=$(json_has "$identity" "publish" "$pub_subject")
  [[ "$sub_subject" != "NONE" ]] && is_sub=$(json_has "$identity" "subscribe" "$sub_subject")

  if [[ "$is_pub" == "true" && "$is_sub" == "true" ]]; then
    echo "PUB+SUB"
  elif [[ "$is_pub" == "true" ]]; then
    echo "PUB"
  elif [[ "$is_sub" == "true" ]]; then
    echo "SUB"
  else
    echo "—"
  fi
}

# ---------------------------------------------------------------------------
# Render the table from JSON
# ---------------------------------------------------------------------------
render_table() {
  # Header
  local header="| Subject |"
  for id in "${IDENTITIES[@]}"; do
    header+=" ${id} |"
  done
  echo "$header"

  # Separator
  local sep="|---|"
  for id in "${IDENTITIES[@]}"; do
    sep+=":-:|"
  done
  echo "$sep"

  # Data rows
  for row in "${ROWS[@]}"; do
    local display="${row%%|*}"
    local rest="${row#*|}"
    local pub_subject="${rest%%|*}"
    local sub_subject="${rest##*|}"
    local line="| ${display} |"
    for id in "${IDENTITIES[@]}"; do
      local val
      val=$(cell_value "$id" "$pub_subject" "$sub_subject")
      line+=" ${val} |"
    done
    echo "$line"
  done
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
  awk '
    /<!-- acl-matrix:begin -->/ { print; found=1; next }
    /<!-- acl-matrix:end -->/ { found=0; while ((getline line < RENDERED) > 0) print line; print; next }
    !found { print }
  ' RENDERED="$RENDERED" "$SPEC" > "${NATS_TMPDIR}/spec_updated.txt"
  cp "${NATS_TMPDIR}/spec_updated.txt" "$SPEC"
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
