#!/usr/bin/env bash
# Smoke test for the LLM E2E path: publish LlmRequest → assert LlmResponse(ok=true).
#
# Usage: tools/smoke_llm_e2e.sh [--timeout SECONDS] [--help]
#
# Env:
#   NATS_URL   NATS server URL (default: nats://roxabituwer:4222)
#
# Expected output on success:
#   [smoke_llm_e2e] ok — LlmResponse received with ok=true
#
# Expected output on failure:
#   [smoke_llm_e2e] FAIL — LlmResponse ok=false (or no reply / timeout)
#
# Troubleshooting:
#   If you see "Permissions Violation" in the broker log: check that the inbox
#   subject (_INBOX.>) and the request subject (lyra.llm.generate.request) are
#   both in the canonical ACL allow-list. See docs/architecture/messaging.md § ACL.
#
# Requires: nats CLI, jq
set -euo pipefail

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

NATS_URL="${NATS_URL:-nats://roxabituwer:4222}"
TIMEOUT=30

# ---------------------------------------------------------------------------
# Arg parsing
# ---------------------------------------------------------------------------

while [[ $# -gt 0 ]]; do
  case "$1" in
    --timeout)
      TIMEOUT="$2"
      shift 2
      ;;
    --help|-h)
      # Print header comment block (lines 2 to the first non-comment line)
      awk 'NR>1 && /^[^#]/{exit} NR>1{sub(/^# ?/,""); print}' "$0"
      exit 0
      ;;
    *)
      echo "[smoke_llm_e2e] Unknown argument: $1" >&2
      exit 1
      ;;
  esac
done

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------

if ! command -v nats >/dev/null 2>&1; then
  echo "[smoke_llm_e2e] FAIL — 'nats' CLI not found. Install from https://github.com/nats-io/natscli/releases" >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "[smoke_llm_e2e] FAIL — 'jq' not found. Install via: apt install jq / brew install jq" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Build payload
# ---------------------------------------------------------------------------

# request_id: alphanumeric, 1-128 chars (LlmRequest constraint)
REQUEST_ID="smoke-$(date +%s)-$$"
# issued_at: ISO 8601 UTC
ISSUED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
# trace_id: arbitrary short identifier
TRACE_ID="smoke-trace-$$"

# TODO(T19): confirm exact model name accepted by the M₁ llmCLI worker;
#            "default" is a safe sentinel — update after T19 live run.
PAYLOAD="$(
  jq -cn \
    --arg contract_version "1" \
    --arg trace_id         "$TRACE_ID" \
    --arg issued_at        "$ISSUED_AT" \
    --arg request_id       "$REQUEST_ID" \
    '{
       contract_version: $contract_version,
       trace_id:         $trace_id,
       issued_at:        $issued_at,
       request_id:       $request_id,
       messages:         [{"role":"user","content":"ping"}],
       model:            "default",
       stream:           false,
       system_prompt:    null,
       max_tokens:       null,
       temperature:      null
     }'
)"

# ---------------------------------------------------------------------------
# Execute
# ---------------------------------------------------------------------------

echo "[smoke_llm_e2e] NATS_URL=${NATS_URL}" >&2
echo "[smoke_llm_e2e] request_id=${REQUEST_ID} timeout=${TIMEOUT}s" >&2

REPLY="$(
  nats req \
    --server "${NATS_URL}" \
    --timeout "${TIMEOUT}s" \
    "lyra.llm.generate.request" \
    "$PAYLOAD" \
    2>&1
)"

# ---------------------------------------------------------------------------
# Parse reply
# ---------------------------------------------------------------------------

# Extract the JSON body (nats req prefixes a header line like:
# "Received on "_INBOX.xxx"" — strip everything before the first '{')
JSON_BODY="$(echo "$REPLY" | sed -n '/^{/,$p' | head -c 65536)"

if [[ -z "$JSON_BODY" ]]; then
  echo "[smoke_llm_e2e] FAIL — no JSON body in reply. Full output:" >&2
  echo "$REPLY" >&2
  exit 1
fi

OK_VAL="$(echo "$JSON_BODY" | jq -r '.ok' 2>/dev/null || echo "parse_error")"

if [[ "$OK_VAL" == "true" ]]; then
  echo "[smoke_llm_e2e] ok — LlmResponse received with ok=true"
  exit 0
else
  echo "[smoke_llm_e2e] FAIL — LlmResponse ok=${OK_VAL} (expected true). Full reply:" >&2
  echo "$JSON_BODY" >&2
  exit 1
fi
