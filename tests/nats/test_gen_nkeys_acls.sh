#!/usr/bin/env bash
# T1.8 — Shell test harness for gen-nkeys.sh --template-only
# Runs without sudo. No filesystem writes outside stdout.
# Usage: bash tests/nats/test_gen_nkeys_acls.sh
set -euo pipefail

cd "$(dirname "$0")/../.."

OUT=$(mktemp)
trap 'rm -f "$OUT"' EXIT

./deploy/nats/gen-nkeys.sh --template-only > "$OUT"
echo "PASS: template-only produced output ($(wc -l < "$OUT") lines)"

# (a) all 7 identity comment labels present — one per user block
count=$(grep -cE '^\s+#\s+(hub|telegram-adapter|discord-adapter|tts-adapter|stt-adapter|llm-worker|monitor)$' "$OUT" || true)
[ "$count" -eq 7 ] \
  || { echo "FAIL (a): expected 7 identity blocks, got ${count}"; exit 1; }
echo "PASS (a): 7 identity blocks found"

# (b) per-identity publish allow-list spot-checks
#   hub must include all 5 publish subjects
hub_section=$(awk '/# hub$/,/allow_responses/' "$OUT")
for subj in 'lyra.outbound.telegram.>' 'lyra.outbound.discord.>' \
            'lyra.voice.tts.request' 'lyra.voice.stt.request' 'lyra.llm.request'; do
  echo "$hub_section" | grep -qF "\"${subj}\"" \
    || { echo "FAIL (b): hub publish missing ${subj}"; exit 1; }
done
#   telegram-adapter publish
tg_section=$(awk '/# telegram-adapter$/,/allow_responses/' "$OUT")
for subj in 'lyra.inbound.telegram.>' 'lyra.system.ready'; do
  echo "$tg_section" | grep -qF "\"${subj}\"" \
    || { echo "FAIL (b): telegram-adapter publish missing ${subj}"; exit 1; }
done
#   llm-worker publish
llm_section=$(awk '/# llm-worker$/,/allow_responses/' "$OUT")
echo "$llm_section" | grep -qF '"lyra.llm.health.*"' \
  || { echo "FAIL (b): llm-worker publish missing lyra.llm.health.*"; exit 1; }
echo "PASS (b): per-identity publish allow-lists match matrix"

# (c) hub subscribe includes _INBOX.>
echo "$hub_section" | grep -qF '"_INBOX.>"' \
  || { echo "FAIL (c): hub subscribe missing _INBOX.>"; exit 1; }
#   hub subscribe includes lyra.llm.health.*
echo "$hub_section" | grep -qF '"lyra.llm.health.*"' \
  || { echo "FAIL (c): hub subscribe missing lyra.llm.health.*"; exit 1; }
echo "PASS (c): hub subscribe includes _INBOX.> and lyra.llm.health.*"

# (d) allow_responses: true present exactly 7 times (once per user block)
ar_count=$(grep -c 'allow_responses: true' "$OUT" || true)
[ "$ar_count" -eq 7 ] \
  || { echo "FAIL (d): expected 7 allow_responses: true lines, got ${ar_count}"; exit 1; }
echo "PASS (d): allow_responses: true appears 7 times"

# (e) the word 'plugin' must not appear anywhere in the generated conf
if grep -qi 'plugin' "$OUT"; then
  echo "FAIL (e): unexpected 'plugin' reference in output"
  grep -ni 'plugin' "$OUT"
  exit 1
fi
echo "PASS (e): no 'plugin' reference in output"

echo ""
echo "PASS: all 5 assertions"
