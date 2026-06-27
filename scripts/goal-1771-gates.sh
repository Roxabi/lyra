#!/usr/bin/env bash
# goal-1771-gates.sh — thin wrapper; canonical gate is `make qg`.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRATCH="${GOAL_1771_SCRATCH:-/tmp/grok-goal-a378c4fde0cd/implementer}"
mkdir -p "${SCRATCH}"
LOG="${SCRATCH}/b3-qg.log"

cd "${REPO_ROOT}"
echo "=== make qg $(date -Iseconds) ===" | tee "${LOG}"
make qg 2>&1 | tee -a "${LOG}"
echo "=== make qg PASS $(date -Iseconds) ===" | tee -a "${LOG}"