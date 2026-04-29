#!/usr/bin/env bash
# check-acl-matrix-retired.sh — validate lifecycle fields on all acl-matrix.json identities.
# Exit 0 → all valid. Exit 1 → one or more errors printed to stdout.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JSON="${REPO_ROOT}/deploy/nats/acl-matrix.json"
DATE_RE='^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
errors=0

while IFS= read -r name; do
  status=$(jq -r --arg n "$name" '.identities[$n].status // empty' "$JSON")
  created=$(jq -r --arg n "$name" '.identities[$n].created_at // empty' "$JSON")

  [ -n "$status" ]  || { echo "ERROR: '$name' missing status";     errors=$((errors + 1)); }
  [ -n "$created" ] || { echo "ERROR: '$name' missing created_at"; errors=$((errors + 1)); }
  [[ -z "$created" || "$created" =~ $DATE_RE ]] || { echo "ERROR: '$name' created_at invalid format: $created (expected YYYY-MM-DD)"; errors=$((errors + 1)); }

  if [ "$status" = "retired" ]; then
    retired=$(jq -r --arg n "$name" '.identities[$n].retired_at // empty' "$JSON")
    [ -n "$retired" ] || { echo "ERROR: '$name' is retired but missing retired_at"; errors=$((errors + 1)); }
    [[ -z "$retired" || "$retired" =~ $DATE_RE ]] || { echo "ERROR: '$name' retired_at invalid format: $retired (expected YYYY-MM-DD)"; errors=$((errors + 1)); }  # -z guard intentional: empty already caught above
    in_flows=$(jq -r --arg n "$name" \
      '[.request_reply_flows[]? | select(.requester==$n or .responder==$n)] | length' "$JSON")
    [ "$in_flows" = "0" ] || { echo "ERROR: '$name' is retired but still referenced in request_reply_flows"; errors=$((errors + 1)); }
  fi
done < <(jq -r '.identities | keys_unsorted[]' "$JSON")

if [ "$errors" -eq 0 ]; then
  echo "ok — acl-matrix lifecycle fields valid"
else
  exit 1
fi
