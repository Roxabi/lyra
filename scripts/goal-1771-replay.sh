#!/usr/bin/env bash
# goal-1771-replay.sh — rebuild AC5 block-order git history from 07c35b36 slices.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRATCH="${GOAL_1771_SCRATCH:-/tmp/grok-goal-a378c4fde0cd/implementer}"
PARENT="${GOAL_1771_PARENT:-63760bde}"
SOURCE="${GOAL_1771_SOURCE:-07c35b36}"
CHERRY_START="${GOAL_1771_CHERRY_START:-b9dc8953}"
CHERRY_END="${GOAL_1771_CHERRY_END:-HEAD}"
REPLAY_WT="${GOAL_1771_REPLAY_WT:-/tmp/grok-goal-1771-replay-wt}"
BRANCH="${GOAL_1771_REPLAY_BRANCH:-goal-1771-ac5-replay}"

MANIFEST="${REPO_ROOT}/scripts/goal-1771-block-manifest.txt"
ASSETS="${REPO_ROOT}/scripts/goal-1771-replay-assets"
PLAN_SLICE="${REPO_ROOT}/scripts/goal-1771-plan-slice.py"

mkdir -p "${SCRATCH}"

manifest_paths() {
  local block="$1"
  awk -v target="$block" '
    $0 == "# " target { on=1; next }
    /^# / { on=0 }
    on && NF && $0 !~ /^#/ { print }
  ' "${MANIFEST}"
}

plan_write() {
  local stage="$1"
  mkdir -p artifacts/plans
  uv run python "${PLAN_SLICE}" "${stage}" > artifacts/plans/1771-factory-dashboard-goal.md
}

capture_block_order() {
  local out="$1"
  {
    echo "=== ${out} $(date -Iseconds) ==="
    echo "commit: $(git rev-parse --short HEAD)"
    echo "message: $(git log -1 --format=%s)"
    echo ""
    git show "HEAD:artifacts/plans/1771-factory-dashboard-goal.md" \
      | grep -nE 'Block [123]|Statut|\- \[[ x]\]' || true
  } > "${SCRATCH}/${out}"
}

checkout_manifest() {
  local block="$1"
  while IFS= read -r path; do
    [[ -n "${path}" ]] || continue
    git checkout "${SOURCE}" -- "${path}"
  done < <(manifest_paths "${block}")
}

apply_b1_assets() {
  cp "${ASSETS}/hub_client.b1.py" src/factory/dashboard/hub_client.py
  cp "${ASSETS}/bff.b1.py" src/factory/dashboard/routes/bff.py
  cp "${ASSETS}/CockpitLayout.b1.tsx" apps/dashboard/src/components/CockpitLayout.tsx
  cp "${ASSETS}/api.b1.ts" apps/dashboard/src/lib/api.ts
  cp "${ASSETS}/CockpitLayout.test.b1.tsx" apps/dashboard/src/components/CockpitLayout.test.tsx
}

cherry_pick_with_theirs() {
  local sha="$1"
  if git cherry-pick "${sha}"; then
    return 0
  fi
  local conflicts
  conflicts=$(git diff --name-only --diff-filter=U || true)
  if [[ -z "${conflicts}" ]]; then
    echo "cherry-pick failed at ${sha} (no conflicted files)" | tee -a "${SCRATCH}/replay.log"
    git cherry-pick --abort || true
    return 1
  fi
  echo "==> auto-resolve ${sha} with --theirs: ${conflicts}" | tee -a "${SCRATCH}/replay.log"
  while IFS= read -r f; do
    [[ -n "${f}" ]] || continue
    git checkout --theirs -- "${f}"
    git add "${f}"
  done <<< "${conflicts}"
  GIT_EDITOR=true git cherry-pick --continue
}

echo "=== goal-1771-replay $(date -Iseconds) ===" | tee "${SCRATCH}/replay.log"

cd "${REPO_ROOT}"
git worktree remove -f "${REPLAY_WT}" 2>/dev/null || true
git branch -D "${BRANCH}" 2>/dev/null || true
git worktree add -B "${BRANCH}" "${REPLAY_WT}" "${PARENT}"

cd "${REPLAY_WT}"

# init plan
plan_write init
git add artifacts/plans/1771-factory-dashboard-goal.md
git commit -m "docs(1771): init plan — all blocks not_started"

plan_write pre_in_progress
git add artifacts/plans/1771-factory-dashboard-goal.md
git commit -m "docs(1771): pre-flight in_progress"

checkout_manifest PRE
plan_write pre_done
git add -A
git commit -m "feat(1771): pre-flight — import-linter, contracts, Makefile, quadlet"

plan_write b1_in_progress
git add artifacts/plans/1771-factory-dashboard-goal.md
git commit -m "docs(1771): block 1 in_progress"

checkout_manifest BLOCK1
apply_b1_assets
plan_write b1_done
git add -A
git commit -m "feat(1771): block 1 — cockpit SPA, chat, harness/model, Docker/CI"
capture_block_order block-order-check-1.txt

plan_write b2_in_progress
git add artifacts/plans/1771-factory-dashboard-goal.md
git commit -m "docs(1771): block 2 in_progress"

checkout_manifest BLOCK2
# restore full B1 files superseded by B2-era versions from monolith
git checkout "${SOURCE}" -- \
  src/factory/dashboard/hub_client.py \
  src/factory/dashboard/routes/bff.py \
  apps/dashboard/src/components/CockpitLayout.tsx \
  apps/dashboard/src/lib/api.ts \
  apps/dashboard/src/components/CockpitLayout.test.tsx
plan_write b2_done
git add -A
git commit -m "feat(1771): block 2 — SessionCatalog hub RPC, Reprendre, ACL"
capture_block_order block-order-check-2.txt

plan_write b3_in_progress
git add artifacts/plans/1771-factory-dashboard-goal.md
git commit -m "docs(1771): block 3 in_progress"

checkout_manifest BLOCK3
plan_write b3_done
git add -A
git commit -m "feat(1771): block 3 — E2E visual, docs, hardening"

echo "==> cherry-pick ${CHERRY_START}..${CHERRY_END}" | tee -a "${SCRATCH}/replay.log"
for sha in $(git -C "${REPO_ROOT}" rev-list --reverse "${CHERRY_START}^..${CHERRY_END}"); do
  if [[ "${sha}" == "${SOURCE}" ]]; then
    continue
  fi
  cherry_pick_with_theirs "${sha}" || {
    echo "cherry-pick failed at ${sha}" | tee -a "${SCRATCH}/replay.log"
    exit 1
  }
done

echo "==> reset staging to ${BRANCH}" | tee -a "${SCRATCH}/replay.log"
cd "${REPO_ROOT}"
git checkout staging
git reset --hard "${BRANCH}"
echo "=== goal-1771-replay PASS $(date -Iseconds) ===" | tee -a "${SCRATCH}/replay.log"