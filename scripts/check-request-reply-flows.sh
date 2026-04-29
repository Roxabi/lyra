#!/usr/bin/env bash
# Validate that every request_reply_flows entry in acl-matrix.json has a
# corresponding identity in the identities map (so the derived
# _inbox.{requester}.> grant has a target). Also embeds regression guards.
#
# Exit 0 = all flows resolvable.
# Exit 1 = drift detected.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JSON="${REPO_ROOT}/deploy/nats/acl-matrix.json"

check_flows() {
  local matrix_file="$1"
  local failures=0

  # For each flow, assert both requester and responder exist as identities
  while IFS= read -r flow; do
    local requester responder req_exists res_exists
    requester=$(echo "${flow}" | jq -r '.requester')
    responder=$(echo "${flow}" | jq -r '.responder')

    req_exists=$(jq -r --arg n "${requester}" '.identities | has($n)' "${matrix_file}")
    res_exists=$(jq -r --arg n "${responder}" '.identities | has($n)' "${matrix_file}")

    if [ "${req_exists}" != "true" ]; then
      echo "FAIL: requester '${requester}' not found in identities"
      failures=$((failures + 1))
    fi
    if [ "${res_exists}" != "true" ]; then
      echo "FAIL: responder '${responder}' not found in identities"
      failures=$((failures + 1))
    fi

    # Verify requester's publish array covers the flow subject (NATS wildcard-aware)
    # A grant covers a subject if: exact match, OR grant ends with .> and the subject
    # equals or starts with the grant prefix (treating .> as "this level and below").
    local subject sub_covered
    subject=$(echo "${flow}" | jq -r '.subject // empty')
    if [ -n "${subject}" ] && [ "${req_exists}" = "true" ]; then
      sub_covered=$(jq -r --arg n "${requester}" --arg s "${subject}" \
        '.identities[$n].publish | map(
          . as $g |
          $g == $s or
          (($g | endswith(".>")) and (
            $s == ($g | .[0:-2]) or ($s | startswith($g | .[0:-1]))
          ))
        ) | any' "${matrix_file}")
      if [ "${sub_covered}" != "true" ]; then
        echo "FAIL: requester '${requester}' publish[] does not cover subject '${subject}'"
        failures=$((failures + 1))
      fi
    fi
  done < <(jq -c '.request_reply_flows // [] | .[]' "${matrix_file}")

  return $((failures > 0 ? 1 : 0))
}

# Verify derivation produces expected grants in gen-nkeys.sh output.
# Accepts an optional matrix_file path; passes it via --matrix to gen-nkeys.sh.
# TODO(#1000): check is global (grep on full output). When --template-only emits
#   machine-parseable JSON, tighten to per-responder block assertion.
check_derived_output() {
  local matrix_file="${1:-${JSON}}"
  local gensh="${REPO_ROOT}/deploy/nats/gen-nkeys.sh"
  [ -x "${gensh}" ] || { echo "FAIL: gen-nkeys.sh not executable at ${gensh}"; return 1; }

  local template_output
  template_output=$("${gensh}" --template-only --matrix "${matrix_file}" 2>/dev/null) || {
    echo "FAIL: gen-nkeys.sh --template-only failed"
    return 1
  }

  local failures=0
  while IFS= read -r flow; do
    local requester responder grant
    requester=$(echo "${flow}" | jq -r '.requester')
    responder=$(echo "${flow}" | jq -r '.responder')
    grant="_inbox.${requester}.>"
    if ! echo "${template_output}" | grep -qF "\"${grant}\""; then
      echo "FAIL: derived grant '${grant}' for responder '${responder}' not found in gen-nkeys.sh output"
      failures=$((failures + 1))
    fi
  done < <(jq -c '.request_reply_flows // [] | .[]' "${matrix_file}")

  return $((failures > 0 ? 1 : 0))
}

# Main check
if ! check_flows "${JSON}"; then
  echo "check-request-reply-flows: FAIL"
  exit 1
fi
echo "check-request-reply-flows: OK ($(jq '.request_reply_flows | length' "${JSON}") flows)"

# Postcondition: verify derivation produces expected grants in gen-nkeys.sh output
if ! check_derived_output "${JSON}"; then
  echo "check-derived-output: FAIL"
  exit 1
fi
echo "check-derived-output: OK"

# Regression guard 1: unknown responder must fail (tests res_exists path)
TMP=$(mktemp)
TMP2=$(mktemp)
trap 'rm -f "${TMP}" "${TMP2}"' EXIT

jq '.request_reply_flows += [{"requester":"hub","responder":"nonexistent-worker","subject":"lyra.test"}]' "${JSON}" > "${TMP}"
guard_rc=0; guard_out=$(check_flows "${TMP}" 2>&1) || guard_rc=$?
if [ "${guard_rc}" -eq 0 ]; then
  echo "FAIL: regression guard 1 did not catch unknown responder"
  echo "  check_flows output: ${guard_out}"
  exit 1
fi
echo "regression-guard-1: OK (correctly detected unknown responder)"

# Regression guard 2: unknown requester must fail (tests req_exists path)
jq '.request_reply_flows += [{"requester":"nonexistent-requester","responder":"hub","subject":"lyra.test"}]' "${JSON}" > "${TMP2}"
guard_rc=0; guard_out=$(check_flows "${TMP2}" 2>&1) || guard_rc=$?
if [ "${guard_rc}" -eq 0 ]; then
  echo "FAIL: regression guard 2 did not catch unknown requester"
  echo "  check_flows output: ${guard_out}"
  exit 1
fi
echo "regression-guard-2: OK (correctly detected unknown requester)"
