#!/usr/bin/env bash
# Aggregate CI job results for the required "ci" check context.
# success and skipped are OK; failure and cancelled fail the aggregate.
# See artifacts/specs/2249-ci-diff-scoped-plan-phase-0-1-spec.mdx (phase 0).
set -euo pipefail

if [[ $# -eq 0 ]]; then
  echo "usage: ci-aggregate-results.sh <result>..." >&2
  exit 2
fi

for result in "$@"; do
  case "$result" in
    success | skipped) ;;
    *)
      echo "::error::aggregate ci: needed job result '${result}' is not success or skipped" >&2
      exit 1
      ;;
  esac
done
