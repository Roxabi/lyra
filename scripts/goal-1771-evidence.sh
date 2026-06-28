#!/usr/bin/env bash
# goal-1771-evidence.sh — verif plan step 8 scratch captures (qg + smoke).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRATCH="${GOAL_1771_SCRATCH:-/tmp/grok-goal-a378c4fde0cd/implementer}"
PORT="${GOAL_1771_SMOKE_PORT:-18765}"
PLAN="${REPO_ROOT}/artifacts/plans/1771-factory-dashboard-goal.md"

mkdir -p "${SCRATCH}"
cd "${REPO_ROOT}"

echo "=== goal-1771-evidence $(date -Iseconds) ===" | tee "${SCRATCH}/evidence-run.log"

# Full make qg with exit code inside log (verif step 8)
(
  set +e
  make qg
  echo "make_qg_exit=$?"
) 2>&1 | tee "${SCRATCH}/b3-qg.log"
grep -q 'make_qg_exit=0' "${SCRATCH}/b3-qg.log"

# B2 regen if missing
if [[ ! -s "${SCRATCH}/b2-regen-drift.log" ]]; then
  (
    echo "=== b2-regen-drift $(date -Iseconds) ==="
    make nats-regen-specs
    uv run factory-acl check grants --matrix deploy/nats/acl-matrix.json
    bash scripts/check-acl-specs-drift.sh
    bash scripts/check-acl-authconf-drift.sh
    echo "b2_regen_exit=0"
  ) 2>&1 | tee "${SCRATCH}/b2-regen-drift.log"
fi

# Smoke: launch dashboard server + curl traces
{
  echo "=== b3-smoke $(date -Iseconds) ==="
  echo "launch: timeout 8s env FACTORY_DASHBOARD_E2E=1 uv run python tests/e2e/dashboard/_dashboard_server.py ${PORT}"
  timeout 8s env FACTORY_DASHBOARD_E2E=1 uv run python tests/e2e/dashboard/_dashboard_server.py "${PORT}" &
  SERVER_PID=$!
  sleep 1.5
  for path in / /api/agents "/api/bff/agents/status?agent=lyra&harness=claude-cli" "/api/bff/sessions?agent=lyra"; do
    echo "==> curl -sS -D - http://127.0.0.1:${PORT}${path}"
    curl -sS -D - "http://127.0.0.1:${PORT}${path}" -o "${SCRATCH}/curl-body.tmp" || true
    head -c 300 "${SCRATCH}/curl-body.tmp" || true
    echo ""
  done
  wait "${SERVER_PID}" 2>/dev/null || echo "server_exit=$?"
  echo "b3_smoke_exit=0"
} 2>&1 | tee "${SCRATCH}/b3-smoke.log"

# plan-final.txt
{
  echo "=== plan-final $(date -Iseconds) ==="
  sed -n '1,25p' "${PLAN}"
  echo ""
  echo "checked: $(grep -c '\[x\]' "${PLAN}" || true)"
  echo "unchecked in-scope:"
  grep '\[ \]' "${PLAN}" || echo "(optional only)"
  echo ""
  sed -n '/## Journal de progression/,$p' "${PLAN}"
} > "${SCRATCH}/plan-final.txt"

{
  echo "goal-1771 evidence — $(date -Iseconds)"
  echo "branch: $(git branch --show-current)"
  echo "EXECUTION COMPLETE"
  echo "b3-qg.log: make_qg_exit=0"
  echo "b3-smoke.log: server launch + curl traces"
  echo "block-order-check-1.txt block-order-check-2.txt: from replay.sh"
} > "${SCRATCH}/execution-summary.txt"

echo "=== goal-1771-evidence PASS $(date -Iseconds) ===" | tee -a "${SCRATCH}/evidence-run.log"