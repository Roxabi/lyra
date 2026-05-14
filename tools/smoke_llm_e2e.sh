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
      # F5: validate --timeout is a positive integer
      [[ "$TIMEOUT" =~ ^[0-9]+$ ]] || { echo "[smoke_llm_e2e] FAIL — --timeout must be a positive integer, got: $TIMEOUT" >&2; exit 1; }
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

# F2: scrub userinfo credentials before echoing NATS_URL to stderr
SCRUBBED_URL="$(echo "$NATS_URL" | sed 's|://[^:]*:[^@]*@|://***:***@|')"
echo "[smoke_llm_e2e] NATS_URL=$SCRUBBED_URL" >&2
echo "[smoke_llm_e2e] request_id=${REQUEST_ID} timeout=${TIMEOUT}s" >&2

# F3: capture stderr separately so nats exit codes surface and creds are scrubbed
REPLY_ERR=$(mktemp)
trap 'rm -f "$REPLY_ERR"' EXIT
if ! REPLY="$(nats req lyra.llm.generate.request "$PAYLOAD" --timeout="${TIMEOUT}s" --server "$NATS_URL" 2>"$REPLY_ERR")"; then
    echo "[smoke_llm_e2e] FAIL — nats req exited non-zero" >&2
    sed 's|nats://[^:]*:[^@]*@|nats://***:***@|g' "$REPLY_ERR" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Parse reply
# ---------------------------------------------------------------------------

# Extract the JSON body (nats req prefixes a header line like:
# "Received on "_INBOX.xxx"" — strip everything before the first '{')
# F18: --raw not supported on the installed nats CLI version; sed ,$p is the
#      only portable extraction method. set +o pipefail guards the pipeline
#      because sed -n returns 0 even on empty input but head may SIGPIPE.
set +o pipefail
JSON_BODY="$(echo "$REPLY" | sed -n '/^{/,$p' | head -c 65536)"
set -o pipefail

if [[ -z "$JSON_BODY" ]]; then
  echo "[smoke_llm_e2e] FAIL — no JSON body in reply. Full output:" >&2
  # F8: scrub any embedded nats credentials before echoing REPLY to stderr
  echo "$REPLY" | sed 's|nats://[^:]*:[^@]*@|nats://***:***@|g' >&2
  exit 1
fi

OK_VAL="$(echo "$JSON_BODY" | jq -r '.ok' 2>/dev/null || echo "parse_error")"

if [[ "$OK_VAL" == "true" ]]; then
  echo "[smoke_llm_e2e] ok — LlmResponse received with ok=true"
  exit 0
else
  echo "[smoke_llm_e2e] FAIL — LlmResponse ok=${OK_VAL} (expected true). Full reply:" >&2
  # F8: scrub any embedded nats credentials before echoing failure output
  echo "$JSON_BODY" | sed 's|nats://[^:]*:[^@]*@|nats://***:***@|g' >&2
  exit 1
fi
