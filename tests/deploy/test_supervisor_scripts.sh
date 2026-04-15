#!/usr/bin/env bash
# Tests for supervisor wrapper scripts — seed-path guard (issue #736).
# Runs without sudo, no NATS daemon, no Python venv required.
# Usage: bash tests/deploy/test_supervisor_scripts.sh
set -euo pipefail

cd "$(dirname "$0")/../.."

HUB_SCRIPT="deploy/supervisor/scripts/run_hub.sh"
ADAPTER_SCRIPT="deploy/supervisor/scripts/run_adapter.sh"
BAD_SEED="/nonexistent/definitely-not-there/seed"

SCRATCH=$(mktemp -d)
trap 'rm -rf "$SCRATCH"' EXIT

# ── T1: both scripts exist and are executable ─────────────────────────────────
[ -f "$HUB_SCRIPT" ]     || { echo "FAIL T1: $HUB_SCRIPT not found";     exit 1; }
[ -x "$HUB_SCRIPT" ]     || { echo "FAIL T1: $HUB_SCRIPT not executable"; exit 1; }
[ -f "$ADAPTER_SCRIPT" ] || { echo "FAIL T1: $ADAPTER_SCRIPT not found";     exit 1; }
[ -x "$ADAPTER_SCRIPT" ] || { echo "FAIL T1: $ADAPTER_SCRIPT not executable"; exit 1; }
echo "PASS T1: both wrapper scripts exist and are executable"

# ── T2: syntax check ──────────────────────────────────────────────────────────
bash -n "$HUB_SCRIPT"     || { echo "FAIL T2: syntax error in $HUB_SCRIPT";     exit 1; }
bash -n "$ADAPTER_SCRIPT" || { echo "FAIL T2: syntax error in $ADAPTER_SCRIPT"; exit 1; }
echo "PASS T2: bash -n syntax OK for both scripts"

# ── T3: guard fires for run_hub.sh with bad seed path ────────────────────────
SCRATCH_HUB=$(mktemp -d)
trap 'rm -rf "$SCRATCH_HUB"' EXIT
ERR_HUB=$(env -i HOME="$SCRATCH_HUB" NATS_NKEY_SEED_PATH="$BAD_SEED" \
  bash "$HUB_SCRIPT" 2>&1 || true)
RC_HUB=$(env -i HOME="$SCRATCH_HUB" NATS_NKEY_SEED_PATH="$BAD_SEED" \
  bash "$HUB_SCRIPT" 2>/dev/null; echo $?) || true
[ "${RC_HUB:-0}" -ne 0 ] || { echo "FAIL T3: run_hub.sh exited 0, expected non-zero"; exit 1; }
echo "$ERR_HUB" | grep -q "NATS_NKEY_SEED_PATH set but not readable" \
  || { echo "FAIL T3: expected guard message not found in stderr: $ERR_HUB"; exit 1; }
echo "$ERR_HUB" | grep -q "$BAD_SEED" \
  || { echo "FAIL T3: bad seed path not echoed in stderr: $ERR_HUB"; exit 1; }
echo "PASS T3: run_hub.sh guard fires — non-zero exit + correct stderr"

# ── T4: guard fires for run_adapter.sh telegram with bad seed path ────────────
SCRATCH_ADP=$(mktemp -d)
trap 'rm -rf "$SCRATCH_ADP"' EXIT
ERR_ADP=$(env -i HOME="$SCRATCH_ADP" NATS_NKEY_SEED_PATH="$BAD_SEED" \
  bash "$ADAPTER_SCRIPT" telegram 2>&1 || true)
RC_ADP=$(env -i HOME="$SCRATCH_ADP" NATS_NKEY_SEED_PATH="$BAD_SEED" \
  bash "$ADAPTER_SCRIPT" telegram 2>/dev/null; echo $?) || true
[ "${RC_ADP:-0}" -ne 0 ] || { echo "FAIL T4: run_adapter.sh exited 0, expected non-zero"; exit 1; }
echo "$ERR_ADP" | grep -q "NATS_NKEY_SEED_PATH set but not readable" \
  || { echo "FAIL T4: expected guard message not found in stderr: $ERR_ADP"; exit 1; }
echo "$ERR_ADP" | grep -q "$BAD_SEED" \
  || { echo "FAIL T4: bad seed path not echoed in stderr: $ERR_ADP"; exit 1; }
echo "PASS T4: run_adapter.sh telegram guard fires — non-zero exit + correct stderr"

# ── T5: guard is skipped when NATS_NKEY_SEED_PATH is unset ───────────────────
SCRATCH_UNSET=$(mktemp -d)
trap 'rm -rf "$SCRATCH_UNSET"' EXIT
ERR_UNSET=$(env -i HOME="$SCRATCH_UNSET" bash "$HUB_SCRIPT" 2>&1 || true)
# Script will fail trying to exec missing .venv/bin/lyra — that's expected.
# What must NOT appear is the seed-guard message.
if echo "$ERR_UNSET" | grep -q "NATS_NKEY_SEED_PATH set but not readable"; then
  echo "FAIL T5: guard triggered even though NATS_NKEY_SEED_PATH was unset"
  echo "--- stderr ---"
  echo "$ERR_UNSET"
  exit 1
fi
echo "PASS T5: guard skipped when NATS_NKEY_SEED_PATH is unset (no false-positive)"

echo ""
echo "All 5 tests passed."
