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

# Single-invocation helper — captures stderr + exit code together so both are
# paired atomically (avoids the dual-exec pattern that can mask failures).
# Usage: run_script <home_dir> <seed_env_kv_or_empty> <script> [args...]
#   seed_env_kv: either "NATS_NKEY_SEED_PATH=<path>" or empty string (unset)
# Populates RUN_ERR + RUN_RC globals.
run_script() {
  local home_dir="$1"; shift
  local seed_spec="$1"; shift
  RUN_ERR=""
  RUN_RC=0
  if [ -n "$seed_spec" ]; then
    RUN_ERR=$(env -i HOME="$home_dir" "$seed_spec" bash "$@" 2>&1) || RUN_RC=$?
  else
    RUN_ERR=$(env -i HOME="$home_dir" bash "$@" 2>&1) || RUN_RC=$?
  fi
}

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

# ── T3: guard fires for run_hub.sh with bad seed path ─────────────────────────
mkdir -p "$SCRATCH/hub"
run_script "$SCRATCH/hub" "NATS_NKEY_SEED_PATH=$BAD_SEED" "$HUB_SCRIPT"
[ "$RUN_RC" -ne 0 ] \
  || { echo "FAIL T3: run_hub.sh exited 0, expected non-zero"; exit 1; }
echo "$RUN_ERR" | grep -q "NATS_NKEY_SEED_PATH must point to a readable, non-empty file" \
  || { echo "FAIL T3: expected guard message not found in stderr: $RUN_ERR"; exit 1; }
echo "$RUN_ERR" | grep -q "$BAD_SEED" \
  || { echo "FAIL T3: bad seed path not echoed in stderr: $RUN_ERR"; exit 1; }
echo "PASS T3: run_hub.sh guard fires — non-zero exit + correct stderr"

# ── T4: guard fires for run_adapter.sh telegram with bad seed path ────────────
mkdir -p "$SCRATCH/adp"
run_script "$SCRATCH/adp" "NATS_NKEY_SEED_PATH=$BAD_SEED" "$ADAPTER_SCRIPT" telegram
[ "$RUN_RC" -ne 0 ] \
  || { echo "FAIL T4: run_adapter.sh exited 0, expected non-zero"; exit 1; }
echo "$RUN_ERR" | grep -q "NATS_NKEY_SEED_PATH must point to a readable, non-empty file" \
  || { echo "FAIL T4: expected guard message not found in stderr: $RUN_ERR"; exit 1; }
echo "$RUN_ERR" | grep -q "$BAD_SEED" \
  || { echo "FAIL T4: bad seed path not echoed in stderr: $RUN_ERR"; exit 1; }
echo "PASS T4: run_adapter.sh telegram guard fires — non-zero exit + correct stderr"

# ── T5: guard is skipped when NATS_NKEY_SEED_PATH is unset (both scripts) ─────
# Each script will fail trying to exec the missing .venv/bin/lyra — expected.
# What must NOT appear is the seed-guard message.
mkdir -p "$SCRATCH/unset-hub" "$SCRATCH/unset-adp"
run_script "$SCRATCH/unset-hub" "" "$HUB_SCRIPT"
if echo "$RUN_ERR" | grep -q "NATS_NKEY_SEED_PATH must point to a readable, non-empty file"; then
  echo "FAIL T5 [hub]: guard triggered even though NATS_NKEY_SEED_PATH was unset"
  echo "--- stderr ---"
  echo "$RUN_ERR"
  exit 1
fi
run_script "$SCRATCH/unset-adp" "" "$ADAPTER_SCRIPT" telegram
if echo "$RUN_ERR" | grep -q "NATS_NKEY_SEED_PATH must point to a readable, non-empty file"; then
  echo "FAIL T5 [adapter]: guard triggered even though NATS_NKEY_SEED_PATH was unset"
  echo "--- stderr ---"
  echo "$RUN_ERR"
  exit 1
fi
echo "PASS T5: guard skipped when NATS_NKEY_SEED_PATH is unset (both scripts, no false-positive)"

# ── T6: guard fires for empty-file seed (zero bytes) ──────────────────────────
# Bare [ -r path ] would pass for a 0-byte file — strengthened guard requires -s.
mkdir -p "$SCRATCH/empty"
EMPTY_SEED="$SCRATCH/empty/zero.seed"
: > "$EMPTY_SEED"
run_script "$SCRATCH/empty" "NATS_NKEY_SEED_PATH=$EMPTY_SEED" "$HUB_SCRIPT"
[ "$RUN_RC" -ne 0 ] \
  || { echo "FAIL T6 [hub]: run_hub.sh exited 0 on empty seed, expected non-zero"; exit 1; }
echo "$RUN_ERR" | grep -q "NATS_NKEY_SEED_PATH must point to a readable, non-empty file" \
  || { echo "FAIL T6 [hub]: expected guard message not found in stderr: $RUN_ERR"; exit 1; }
run_script "$SCRATCH/empty" "NATS_NKEY_SEED_PATH=$EMPTY_SEED" "$ADAPTER_SCRIPT" telegram
[ "$RUN_RC" -ne 0 ] \
  || { echo "FAIL T6 [adapter]: run_adapter.sh exited 0 on empty seed, expected non-zero"; exit 1; }
echo "$RUN_ERR" | grep -q "NATS_NKEY_SEED_PATH must point to a readable, non-empty file" \
  || { echo "FAIL T6 [adapter]: expected guard message not found in stderr: $RUN_ERR"; exit 1; }
echo "PASS T6: guard fires for empty-file seed (both scripts)"

# ── T7: guard fires for directory-as-seed ─────────────────────────────────────
# Bare [ -r path ] would pass for a directory — strengthened guard requires -f.
mkdir -p "$SCRATCH/dirseed/not-a-file"
DIR_SEED="$SCRATCH/dirseed/not-a-file"
run_script "$SCRATCH/dirseed" "NATS_NKEY_SEED_PATH=$DIR_SEED" "$HUB_SCRIPT"
[ "$RUN_RC" -ne 0 ] \
  || { echo "FAIL T7 [hub]: run_hub.sh exited 0 on directory seed, expected non-zero"; exit 1; }
echo "$RUN_ERR" | grep -q "NATS_NKEY_SEED_PATH must point to a readable, non-empty file" \
  || { echo "FAIL T7 [hub]: expected guard message not found in stderr: $RUN_ERR"; exit 1; }
run_script "$SCRATCH/dirseed" "NATS_NKEY_SEED_PATH=$DIR_SEED" "$ADAPTER_SCRIPT" telegram
[ "$RUN_RC" -ne 0 ] \
  || { echo "FAIL T7 [adapter]: run_adapter.sh exited 0 on directory seed, expected non-zero"; exit 1; }
echo "$RUN_ERR" | grep -q "NATS_NKEY_SEED_PATH must point to a readable, non-empty file" \
  || { echo "FAIL T7 [adapter]: expected guard message not found in stderr: $RUN_ERR"; exit 1; }
echo "PASS T7: guard fires for directory-as-seed (both scripts)"

echo ""
echo "All 7 tests passed."
