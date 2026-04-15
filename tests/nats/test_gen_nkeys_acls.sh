#!/usr/bin/env bash
# T1.8 — Shell test harness for gen-nkeys.sh --template-only
# Runs without sudo. No filesystem writes outside stdout.
# Usage: bash tests/nats/test_gen_nkeys_acls.sh
#
# Asserts 7 conditions against the --template-only rendered auth.conf:
#   (a) 7 identity blocks exist (one per user in IDENTITIES)
#   (b) each identity's publish allow-list equals its matrix row (set equality)
#   (c) each identity's subscribe allow-list equals its matrix row (set equality)
#   (d) allow_responses: true present on every user (7 occurrences)
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
[ -x "./deploy/nats/gen-nkeys.sh" ] \
  || { echo "FAIL: cannot locate ./deploy/nats/gen-nkeys.sh from $(pwd)"; exit 1; }

OUT=$(mktemp)
trap 'rm -f "$OUT"' EXIT

./deploy/nats/gen-nkeys.sh --template-only > "$OUT"
echo "PASS: template-only produced output ($(wc -l < "$OUT") lines)"

# ── Expected allow-lists (authoritative copy mirrors spec §Data Model matrix) ──
# If the spec matrix changes, update both deploy/nats/gen-nkeys.sh AND this file.
declare -A EXPECTED_PUB EXPECTED_SUB
EXPECTED_PUB[hub]='lyra.outbound.telegram.> lyra.outbound.discord.> lyra.voice.tts.request lyra.voice.stt.request lyra.llm.request'
EXPECTED_SUB[hub]='lyra.inbound.telegram.> lyra.inbound.discord.> lyra.voice.tts.heartbeat lyra.voice.stt.heartbeat lyra.llm.health.* lyra.system.ready _INBOX.>'
EXPECTED_PUB[telegram-adapter]='lyra.inbound.telegram.> lyra.system.ready'
EXPECTED_SUB[telegram-adapter]='lyra.outbound.telegram.>'
EXPECTED_PUB[discord-adapter]='lyra.inbound.discord.> lyra.system.ready'
EXPECTED_SUB[discord-adapter]='lyra.outbound.discord.>'
EXPECTED_PUB[tts-adapter]='lyra.voice.tts.heartbeat'
EXPECTED_SUB[tts-adapter]='lyra.voice.tts.request'
EXPECTED_PUB[stt-adapter]='lyra.voice.stt.heartbeat'
EXPECTED_SUB[stt-adapter]='lyra.voice.stt.request'
EXPECTED_PUB[llm-worker]='lyra.llm.health.*'
EXPECTED_SUB[llm-worker]='lyra.llm.request'
EXPECTED_PUB[monitor]='lyra.monitor.>'
EXPECTED_SUB[monitor]='lyra.monitor.>'

IDENTITIES=(hub telegram-adapter discord-adapter tts-adapter stt-adapter llm-worker monitor)

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
  local actual
  actual=$(echo "$line" | grep -oE '"[^"]+"' | tr -d '"' | sort -u)
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

# ── (a) 7 identity comment labels ──────────────────────────────────────────────
count=$(grep -cE '^[[:space:]]+#[[:space:]]+(hub|telegram-adapter|discord-adapter|tts-adapter|stt-adapter|llm-worker|monitor)$' "$OUT" || true)
[ "$count" -eq 7 ] \
  || { echo "FAIL (a): expected 7 identity blocks, got ${count}"; exit 1; }
echo "PASS (a): 7 identity blocks found"

# ── (b) + (c) + (f) set-equality publish and subscribe for all 7 identities ───
for name in "${IDENTITIES[@]}"; do
  block=$(extract_block "$name")
  [ -n "$block" ] || { echo "FAIL: block not found for ${name}"; exit 1; }
  assert_allow_list_equals "$block" "publish"   "${EXPECTED_PUB[$name]}" "$name"
  assert_allow_list_equals "$block" "subscribe" "${EXPECTED_SUB[$name]}" "$name"
done
echo "PASS (b): publish allow-lists match matrix (set equality, 7 identities)"
echo "PASS (c): subscribe allow-lists match matrix (set equality, 7 identities)"
echo "PASS (f): no over-privilege detected"

# ── (d) allow_responses: true present on every user (7 occurrences) ───────────
ar_count=$(grep -c 'allow_responses: true' "$OUT" || true)
[ "$ar_count" -eq 7 ] \
  || { echo "FAIL (d): expected 7 allow_responses: true lines, got ${ar_count}"; exit 1; }
echo "PASS (d): allow_responses: true appears 7 times"

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
echo "PASS: all 7 assertions (a–g) — 7 identities × {pub,sub} × set equality"
