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

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JSON="${REPO_ROOT}/deploy/nats/acl-matrix.json"
SPEC="${REPO_ROOT}/artifacts/specs/706-per-role-nkeys-acls-spec.mdx"

# ---------------------------------------------------------------------------
# Identity column order — all active identities (updated: retired tts-adapter/sst-adapter
# removed, voice-tts/voice-stt/image-worker/clipool-worker added per postmortem Fix 1+2)
# ---------------------------------------------------------------------------
IDENTITIES=(hub telegram-adapter discord-adapter voice-tts voice-stt llm-worker image-worker clipool-worker monitor)

# ---------------------------------------------------------------------------
# Subject rows — "display_label|json_pub_subject|json_sub_subject"
#
# json_pub_subject: the exact JSON publish subject to check for PUB.
#   May differ from display (e.g. spec row "lyra.voice.tts.request" but hub
#   publishes "lyra.voice.tts.request.>" in JSON).  Use "NONE" if no publish
#   subject is relevant for this row.
# json_sub_subject: exact JSON subscribe subject for SUB. "NONE" if n/a.
#
# Inbox subjects use lowercase _inbox.X.> (ADR-051 + postmortem Fix 1).
# ---------------------------------------------------------------------------
# Format: "display|pub_subject|sub_subject"
ROWS=(
  '`lyra.inbound.telegram.>`|lyra.inbound.telegram.>|lyra.inbound.telegram.>'
  '`lyra.inbound.discord.>`|lyra.inbound.discord.>|lyra.inbound.discord.>'
  '`lyra.outbound.telegram.>`|lyra.outbound.telegram.>|lyra.outbound.telegram.>'
  '`lyra.outbound.discord.>`|lyra.outbound.discord.>|lyra.outbound.discord.>'
  '`lyra.system.ready` [^ready]|lyra.system.ready|lyra.system.ready'
  '`lyra.voice.tts.request`|lyra.voice.tts.request.>|lyra.voice.tts.request'
  '`lyra.voice.tts.heartbeat`|lyra.voice.tts.heartbeat|lyra.voice.tts.heartbeat'
  '`lyra.voice.stt.request`|lyra.voice.stt.request.>|lyra.voice.stt.request'
  '`lyra.voice.stt.heartbeat`|lyra.voice.stt.heartbeat|lyra.voice.stt.heartbeat'
  '`lyra.llm.request`|lyra.llm.request|lyra.llm.request'
  '`lyra.llm.health.*` [^health]|lyra.llm.health.*|lyra.llm.health.*'
  '`lyra.image.generate.request`|lyra.image.generate.request|lyra.image.generate.request'
  '`lyra.image.heartbeat`|lyra.image.heartbeat|lyra.image.heartbeat'
  '`lyra.clipool.cmd`|lyra.clipool.cmd|lyra.clipool.cmd'
  '`lyra.clipool.heartbeat`|lyra.clipool.heartbeat|lyra.clipool.heartbeat'
  '`lyra.audit.>`|lyra.audit.>|NONE'
  '`lyra.monitor.>` [^monitor]|lyra.monitor.>|lyra.monitor.>'
  '`_inbox.hub.>` [^inbox]|_inbox.hub.>|_inbox.hub.>'
  '`_inbox.telegram-adapter.>` [^inbox]|NONE|_inbox.telegram-adapter.>'
  '`_inbox.discord-adapter.>` [^inbox]|NONE|_inbox.discord-adapter.>'
  '`_inbox.voice-tts.>` [^inbox]|NONE|_inbox.voice-tts.>'
  '`_inbox.voice-stt.>` [^inbox]|NONE|_inbox.voice-stt.>'
  '`_inbox.image-worker.>` [^inbox]|NONE|_inbox.image-worker.>'
  '`_inbox.clipool-worker.>` [^inbox]|NONE|_inbox.clipool-worker.>'
)

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
  ' "$JSON"
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

# ---------------------------------------------------------------------------
# Diff — exit 0 on match, 1 on drift
# ---------------------------------------------------------------------------
if diff -u "$SPEC_BLOCK" "$RENDERED"; then
  exit 0
else
  exit 1
fi
