#!/usr/bin/env bash
# T1.8 — Shell test harness for gen_nkeys.py genkeys --template-only
# Runs without sudo. No filesystem writes outside stdout.
# Usage: bash tests/nats/test_gen_nkeys_acls.sh
#
# Asserts 7 conditions against the --template-only rendered auth.conf:
#   (a) N identity blocks exist (one per active user in IDENTITIES)
#   (b) each identity's publish allow-list equals its matrix row (set equality)
#   (c) each identity's subscribe allow-list equals its matrix row (set equality)
#   (d) allow_responses: true present on every user (9 occurrences)
#   (e) no 'plugin' reference anywhere in output
#   (f) no over-privilege: no identity has unexpected extra subjects
#   (g) default_permissions { deny: [">"] } present (defense-in-depth)
#
# SKIP T3.2: synthetic Permissions Violation injection deferred to #716.
# Rationale: journalctl -u filters by SYSTEMD_UNIT; systemd-cat only sets
# SYSLOG_IDENTIFIER. A correct test needs a real nats-server instance
# (single static binary) — tracked in issue #716.
set -euo pipefail

cd "$(dirname "$0")/../.."
# NB16: fail loudly if the cd landed somewhere unexpected (symlinked runner,
# sourced invocation) rather than with a cryptic "No such file" later.
[ -f "./scripts/gen_nkeys.py" ] \
  || { echo "FAIL: cannot locate ./scripts/gen_nkeys.py from $(pwd)"; exit 1; }

OUT=$(mktemp)
trap 'rm -f "$OUT"' EXIT

uv run factory-acl genkeys --template-only > "$OUT"
echo "PASS: template-only produced output ($(wc -l < "$OUT") lines)"

# ── Expected allow-lists loaded from acl-matrix.json (SSoT per #717) ──
MATRIX_JSON="deploy/nats/acl-matrix.json"
[ -f "$MATRIX_JSON" ] || { echo "FAIL: $MATRIX_JSON not found"; exit 1; }
declare -A EXPECTED_PUB EXPECTED_SUB
IDENTITIES=()
# Effective grants (group expansion + flow-inbox injection) — mirrors scripts/_effective.py
while IFS= read -r row; do
  name=$(echo "$row" | jq -r '.name')
  IDENTITIES+=("$name")
  EXPECTED_PUB[$name]=$(echo "$row" | jq -r '.pub | join(" ")')
  EXPECTED_SUB[$name]=$(echo "$row" | jq -r '.sub | join(" ")')
done < <(uv run python -c "
import json
from pathlib import Path
from scripts._loader import load_matrix
from scripts._effective import effective_grants
matrix = load_matrix(Path('deploy/nats/acl-matrix.json'))
for name, (pub, sub) in sorted(effective_grants(matrix).items()):
    print(json.dumps({'name': name, 'pub': pub, 'sub': sub}))
")
IDENTITY_COUNT=${#IDENTITIES[@]}
[ "$IDENTITY_COUNT" -gt 0 ] || { echo "FAIL: no active identities in $MATRIX_JSON"; exit 1; }

# ── extract_block: print the user{} block for a given identity name ────────────
# B9: the closing-brace condition records `entry_depth` when the identity's
# `# <name>` anchor is seen, then exits on the `}` that returns depth to
# `entry_depth - 1` — i.e. the outer user-block close, not the file's final `}`.
# Without this, the awk range leaked from the identity's comment to the end of
# the file (tests still passed by accident due to `head -1` in the caller's
# regex, but the contract was wrong).
extract_block() {
  local name="$1"
  awk -v target="# ${name}" '
    /\{/ { depth++ }
    index($0, target) > 0 && !inblock { inblock = 1; entry_depth = depth }
    inblock { print }
    /\}/ { if (inblock && depth == entry_depth) { exit } ; depth-- }
  ' "$OUT"
}

# ── assert_allow_list_equals: set-equality check on a permissions allow-list ───
# Args:  block_text direction expected_subjects_space_sep identity_name
# Fails with non-zero + diagnostic if: missing expected, or extra unexpected.
assert_allow_list_equals() {
  local block="$1" direction="$2" expected="$3" name="$4"
  # Extract the `<direction>: { allow: [ ... ] }` list content
  local line
  line=$(echo "$block" | grep -oE "${direction}:[[:space:]]*\{[[:space:]]*allow:[[:space:]]*\[[^]]*\]" | head -1)
  if [ -z "$line" ]; then
    echo "FAIL: no ${direction} allow-list found for ${name}"
    echo "--- block ---"
    echo "$block"
    exit 1
  fi
  # Pull quoted subjects; normalize to whitespace-separated tokens.
  # grep exits 1 on empty allow: [] — must not trip set -e.
  local actual
  actual=$(echo "$line" | { grep -oE '"[^"]+"' || true; } | tr -d '"' | sort -u)
  local expected_sorted
  expected_sorted=$(echo "$expected" | tr ' ' '\n' | sort -u)

  # Missing subjects?
  local missing
  missing=$(comm -23 <(echo "$expected_sorted") <(echo "$actual") || true)
  if [ -n "$missing" ]; then
    echo "FAIL: ${name} ${direction} missing subject(s):"
    echo "$missing" | sed 's/^/    /'
    exit 1
  fi

  # Extra (over-privileged) subjects?
  local extra
  extra=$(comm -13 <(echo "$expected_sorted") <(echo "$actual") || true)
  if [ -n "$extra" ]; then
    echo "FAIL: ${name} ${direction} has OVER-PRIVILEGE (extra subject(s) not in matrix):"
    echo "$extra" | sed 's/^/    /'
    exit 1
  fi
}

# ── (a) identity comment labels (active identities from acl-matrix.json) ───────
count=0
for name in "${IDENTITIES[@]}"; do
  grep -qE "^[[:space:]]+#[[:space:]]+${name}$" "$OUT" && count=$((count + 1)) \
    || { echo "FAIL (a): identity block missing for ${name}"; exit 1; }
done
[ "$count" -eq "$IDENTITY_COUNT" ] \
  || { echo "FAIL (a): expected ${IDENTITY_COUNT} identity blocks, got ${count}"; exit 1; }
echo "PASS (a): ${IDENTITY_COUNT} identity blocks found"

# ── (b) + (c) + (f) set-equality publish and subscribe for all identities ─────
for name in "${IDENTITIES[@]}"; do
  block=$(extract_block "$name")
  [ -n "$block" ] || { echo "FAIL: block not found for ${name}"; exit 1; }
  assert_allow_list_equals "$block" "publish"   "${EXPECTED_PUB[$name]}" "$name"
  assert_allow_list_equals "$block" "subscribe" "${EXPECTED_SUB[$name]}" "$name"
done
echo "PASS (b): publish allow-lists match matrix (set equality, ${IDENTITY_COUNT} identities)"
echo "PASS (c): subscribe allow-lists match matrix (set equality, ${IDENTITY_COUNT} identities)"
echo "PASS (f): no over-privilege detected"

# ── (d) allow_responses: true only on identities flagged in acl-matrix.json ───
AR_EXPECTED=$(jq '[.identities | to_entries[] | select(.value.status != "retired" and (.value.allow_responses // false))] | length' "$MATRIX_JSON")
ar_count=$(grep -c 'allow_responses: true' "$OUT" || true)
[ "$ar_count" -eq "$AR_EXPECTED" ] \
  || { echo "FAIL (d): expected ${AR_EXPECTED} allow_responses: true lines, got ${ar_count}"; exit 1; }
echo "PASS (d): allow_responses: true appears ${AR_EXPECTED} times"

# ── (e) the word 'plugin' must not appear anywhere in the generated conf ──────
if grep -qi 'plugin' "$OUT"; then
  echo "FAIL (e): unexpected 'plugin' reference in output"
  grep -ni 'plugin' "$OUT"
  exit 1
fi
echo "PASS (e): no 'plugin' reference in output"

# ── (g) default_permissions deny-all fallback (defense-in-depth, C2) ──────────
# Any future user added without an explicit permissions{} block should default
# to deny-all, not NATS's implicit allow-all. Verifies the generator emits the
# default_permissions stanza with deny: [">"] on both publish and subscribe.
if ! grep -q 'default_permissions' "$OUT"; then
  echo "FAIL (g): default_permissions block missing from authorization {}"
  exit 1
fi
dp_pub=$(awk '/default_permissions:[[:space:]]*\{/,/users:[[:space:]]*\[/' "$OUT" | grep -c 'publish:[[:space:]]*{[[:space:]]*deny:[[:space:]]*\[">"\]' || true)
dp_sub=$(awk '/default_permissions:[[:space:]]*\{/,/users:[[:space:]]*\[/' "$OUT" | grep -c 'subscribe:[[:space:]]*{[[:space:]]*deny:[[:space:]]*\[">"\]' || true)
[ "$dp_pub" -ge 1 ] && [ "$dp_sub" -ge 1 ] \
  || { echo "FAIL (g): default_permissions must deny: [\">\"] on both publish and subscribe"; exit 1; }
echo "PASS (g): default_permissions denies publish + subscribe on \">\" fallback"

echo ""
echo "PASS: all 7 assertions (a–g) — ${IDENTITY_COUNT} identities × {pub,sub} × set equality"

# ── #754 image domain integration — assert image-worker + amended hub ACL ──
# Contract: ADR-050 (absorbed into ADR-049) (lyra ↔ imagecli). See artifacts/specs/754-lyra-image-domain-integration-spec.mdx §Slice 3.
# Reuses $OUT (written at line 30) and the brace-depth-guarded
# extract_block helper above — see B9 rationale for why the guard matters.

# ── (#754-1) image-worker block must be present ───────────────────────────────
grep -qE '# image-worker$' "$OUT" \
  || { echo "FAIL: image-worker block missing"; exit 1; }
echo "PASS (#754-1): image-worker block present"

# ── (#754-2) image-worker publish allow-list ───────────────────────────────────
# Expected: factory.image.heartbeat + _INBOX.> + _inbox.> (defensive inbox entries
# mirror voice-tts/voice-stt for reply-path robustness; see #804 review fix).
iw_block=$(extract_block image-worker)
[ -n "$iw_block" ] || { echo "FAIL: could not extract image-worker block"; exit 1; }

# Must contain factory.image.heartbeat in the publish line
iw_pub_line=$(echo "$iw_block" | grep -E 'publish:[[:space:]]*\{[[:space:]]*allow:' | head -1)
echo "$iw_pub_line" | grep -q '"factory.image.heartbeat"' \
  || { echo "FAIL: image-worker publish must allow factory.image.heartbeat"; exit 1; }

# Must contain hub flow-inbox (request_reply_flows inject _inbox.{requester}.>)
echo "$iw_pub_line" | grep -q '"_inbox.hub.>"' \
  || { echo "FAIL: image-worker publish must allow _inbox.hub.>"; exit 1; }

# Must NOT contain any other factory.* subject in the publish line
extra_pub=$(echo "$iw_pub_line" | grep -oE '"factory\.[^"]+"' | grep -v '"factory\.image\.heartbeat"' || true)
[ -z "$extra_pub" ] \
  || { echo "FAIL: image-worker publish has unexpected factory.* subject(s): ${extra_pub}"; exit 1; }
echo "PASS (#754-2): image-worker publish includes factory.image.heartbeat + _inbox.hub.>"

# ── (#754-3) image-worker subscribe allow-list == ["factory.image.generate.request"] ──
# Must contain factory.image.generate.request in the subscribe line
echo "$iw_block" | grep -E 'subscribe:[[:space:]]*\{[[:space:]]*allow:' \
  | grep -q '"factory.image.generate.request"' \
  || { echo "FAIL: image-worker subscribe must allow factory.image.generate.request"; exit 1; }

# Must NOT contain any other factory.* subject in the subscribe line
iw_sub_line=$(echo "$iw_block" | grep -E 'subscribe:[[:space:]]*\{[[:space:]]*allow:' | head -1)
extra_sub=$(echo "$iw_sub_line" | grep -oE '"factory\.[^"]+"' | grep -v '"factory\.image\.generate\.request"' || true)
[ -z "$extra_sub" ] \
  || { echo "FAIL: image-worker subscribe has unexpected factory.* subject(s): ${extra_sub}"; exit 1; }
echo "PASS (#754-3): image-worker subscribe allow-list == [\"factory.image.generate.request\"]"

# ── (#754-4) hub publish gained factory.image.generate.request ──────────────────
hub_block=$(extract_block hub)
[ -n "$hub_block" ] || { echo "FAIL: could not extract hub block"; exit 1; }

echo "$hub_block" | grep -E 'publish:[[:space:]]*\{[[:space:]]*allow:' \
  | grep -q '"factory.image.generate.request"' \
  || { echo "FAIL: hub publish must include factory.image.generate.request"; exit 1; }
echo "PASS (#754-4): hub publish allow-list includes factory.image.generate.request"

# ── (#754-5) hub subscribe gained factory.image.heartbeat ───────────────────────
echo "$hub_block" | grep -E 'subscribe:[[:space:]]*\{[[:space:]]*allow:' \
  | grep -q '"factory.image.heartbeat"' \
  || { echo "FAIL: hub subscribe must include factory.image.heartbeat"; exit 1; }
echo "PASS (#754-5): hub subscribe allow-list includes factory.image.heartbeat"

# ── (#754-6) no other identity may access factory.image.* ───────────────────────
OTHER_IDENTITIES=()
for other_id in "${IDENTITIES[@]}"; do
  [[ "$other_id" == "hub" || "$other_id" == "image-worker" ]] && continue
  OTHER_IDENTITIES+=("$other_id")
done
for other_id in "${OTHER_IDENTITIES[@]}"; do
  other_block=$(extract_block "$other_id")
  [ -n "$other_block" ] || { echo "FAIL: could not extract block for ${other_id}"; exit 1; }
  leak=$(echo "$other_block" | grep -oE '"factory\.image\.[^"]+"' || true)
  [ -z "$leak" ] \
    || { echo "FAIL: ${other_id} must not have factory.image.* access, found: ${leak}"; exit 1; }
done
echo "PASS (#754-6): no other identity has factory.image.* access"

echo ""
echo "PASS (#754): image-worker ACL + amended hub ACL assertions (5 checks)"

# ── #715 / ADR-051 — per-identity inbox prefix assertions ────────────────────
# For each lyra-owned identity the generated auth.conf MUST contain the
# scoped prefix form and MUST NOT contain the bare wildcard in any allow-list.
#
# Scope:
#   Factory-owned (narrowed this PR): hub, telegram-adapter, discord-adapter,
#                                   tts-adapter, stt-adapter
#   Satellite (out of scope this PR, unchanged): voice-tts, voice-stt, image-worker
#
# Lowercase _inbox.<identity>.> is required for tts-adapter and stt-adapter
# because both rows carried _inbox.> defensively (nats-py case sensitivity).

INBOX_SCOPED_IDENTITIES=(hub telegram-adapter discord-adapter web-adapter)

for identity in "${INBOX_SCOPED_IDENTITIES[@]}"; do
  id_block=$(extract_block "$identity")
  [ -n "$id_block" ] || { echo "FAIL (#715): could not extract block for ${identity}"; exit 1; }

  # Assert scoped inbox subject present in subscribe allow-list (lowercase per ADR-051)
  scoped_inbox="_inbox.${identity}.>"
  echo "$id_block" | grep -E 'subscribe:[[:space:]]*\{[[:space:]]*allow:' \
    | grep -qF "\"${scoped_inbox}\"" \
    || { echo "FAIL (#715): ${identity} subscribe must contain \"${scoped_inbox}\""; exit 1; }

  # Assert bare wildcards NOT present in subscribe allow-list
  for bare in '_INBOX.>' '_inbox.>'; do
    echo "$id_block" | grep -E 'subscribe:[[:space:]]*\{[[:space:]]*allow:' \
      | grep -qF "\"${bare}\"" \
      && { echo "FAIL (#715): ${identity} subscribe must NOT contain bare \"${bare}\""; exit 1; } || true
  done

  echo "PASS (#715): ${identity} subscribe has \"${scoped_inbox}\" (no bare inbox wildcards)"
done

# Satellite workers: flow-inbox grants on publish (_inbox.{requester}.> from request_reply_flows)
for identity in voice-tts voice-stt image-worker; do
  id_block=$(extract_block "$identity")
  [ -n "$id_block" ] || { echo "FAIL (#715): could not extract block for ${identity}"; exit 1; }
  echo "$id_block" | grep -E 'publish:[[:space:]]*\{[[:space:]]*allow:' \
    | grep -qF '"_inbox.hub.>"' \
    || { echo "FAIL (#715-satellite): ${identity} publish must contain \"_inbox.hub.>\" (hub flow-inbox)"; exit 1; }
  echo "PASS (#715-satellite): ${identity} publish has \"_inbox.hub.>\" (flow-inbox)"
done

echo ""
echo "PASS (#715/ADR-051): per-identity inbox prefix assertions (${#INBOX_SCOPED_IDENTITIES[@]} factory + 3 satellite)"
