#!/usr/bin/env bash
# goal-1771-gates.sh — fail-fast quality gates for #1771 dashboard goal.
# Exit code is the only success signal (no manual qg_exit_code suffix).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRATCH="${GOAL_1771_SCRATCH:-/tmp/grok-goal-a378c4fde0cd/implementer}"
mkdir -p "${SCRATCH}"
LOG="${SCRATCH}/b3-qg.log"

exec > >(tee "${LOG}") 2>&1

echo "=== goal-1771-gates $(date -Iseconds) ==="
cd "${REPO_ROOT}"

echo "==> bun run lint"
bun run lint

echo "==> uv run ruff check ."
uv run ruff check .

echo "==> uv run lint-imports"
uv run lint-imports

echo "==> bun run typecheck"
bun run typecheck

echo "==> bun run build:dashboard"
bun run build:dashboard

echo "==> bun run --filter @roxabi-factory/dashboard test"
bun run --filter @roxabi-factory/dashboard test

echo "==> pytest dashboard paths"
uv run pytest \
  tests/adapters/web/test_dashboard_bff.py \
  tests/adapters/web/test_web_server.py \
  tests/bootstrap/test_dashboard_rpc.py \
  tests/agents/test_simple_agent_web_harness.py \
  tests/e2e/dashboard/ \
  -n0 -q

echo "==> ACL + secrets drift"
bash scripts/check-acl-specs-drift.sh
bash scripts/check-acl-authconf-drift.sh
uv run factory-check-flows
bash tools/check_secrets_drift.sh

echo "==> web_server SLOC"
wc -l src/factory/adapters/web/web_server.py

echo "=== goal-1771-gates PASS $(date -Iseconds) ==="