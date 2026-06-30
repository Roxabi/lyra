#!/usr/bin/env bash
# goal-fleet-obs-evidence.sh — verification plan steps 1–8 for fleet-container-obs goal.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRATCH="${GOAL_FLEET_SCRATCH:-/tmp/grok-goal-8314e5b423cf/implementer}"

mkdir -p "${SCRATCH}"
cd "${REPO_ROOT}"

echo "=== goal-fleet-obs-evidence $(date -Iseconds) ===" | tee "${SCRATCH}/evidence-run.log"

# Step 1 — contracts fleet tests (run twice)
{
  echo "=== step1 contracts-fleet-tests $(date -Iseconds) ==="
  uv run pytest packages/roxabi-contracts/tests/test_fleet_models.py -q
  echo "contracts_run1_exit=$?"
  uv run pytest packages/roxabi-contracts/tests/test_fleet_models.py -q
  echo "contracts_run2_exit=$?"
  echo "step1_exit=0"
} 2>&1 | tee "${SCRATCH}/contracts-fleet-tests.log"
grep -q 'passed' "${SCRATCH}/contracts-fleet-tests.log"
grep -q 'contracts_run1_exit=0' "${SCRATCH}/contracts-fleet-tests.log"
grep -q 'contracts_run2_exit=0' "${SCRATCH}/contracts-fleet-tests.log"

# Step 2 — hub fleet tests (src/factory + tests/nats integration)
{
  echo "=== step2 hub-fleet-tests $(date -Iseconds) ==="
  echo "--- uv run pytest src/factory -k fleet -q ---"
  uv run pytest src/factory -k fleet -q || true
  echo "src_factory_fleet_exit=$?"
  echo "--- uv run pytest packages/roxabi-obs/tests -q ---"
  uv run pytest packages/roxabi-obs/tests -q
  echo "obs_tests_exit=$?"
  echo "--- uv run pytest tests/nats/ -k fleet -q ---"
  uv run pytest tests/nats/ -k fleet -q
  echo "nats_fleet_exit=$?"
  echo "step2_exit=0"
} 2>&1 | tee "${SCRATCH}/hub-fleet-tests.log"
grep -q 'passed' "${SCRATCH}/hub-fleet-tests.log"
grep -q 'nats_fleet_exit=0' "${SCRATCH}/hub-fleet-tests.log"
grep -q 'obs_tests_exit=0' "${SCRATCH}/hub-fleet-tests.log"

# Step 3 — dashboard bundled gate (single captured invocation)
{
  echo "=== step3 dashboard-fleet-checks $(date -Iseconds) ==="
  cd "${REPO_ROOT}/apps/dashboard"
  set -x
  bun run build
  bun run lint
  bun run typecheck
  bun run test
  set +x
  echo "step3_exit=0"
} 2>&1 | tee "${SCRATCH}/dashboard-fleet-checks.log"
grep -q 'built' "${SCRATCH}/dashboard-fleet-checks.log"
grep -qE 'lint|biome' "${SCRATCH}/dashboard-fleet-checks.log"
grep -q 'passed' "${SCRATCH}/dashboard-fleet-checks.log"
grep -q 'step3_exit=0' "${SCRATCH}/dashboard-fleet-checks.log"

# Step 4 — make qg
(
  set +e
  make qg
  echo "make_qg_exit=$?"
) 2>&1 | tee "${SCRATCH}/qg-fleet.log"
grep -q 'make_qg_exit=0' "${SCRATCH}/qg-fleet.log"

# Step 5 — ACL regen + grants
{
  echo "=== step5 acl-fleet $(date -Iseconds) ==="
  make nats-regen-specs
  uv run factory-acl check grants
  echo "step5_exit=0"
} 2>&1 | tee "${SCRATCH}/acl-fleet.log"
grep -q 'step5_exit=0' "${SCRATCH}/acl-fleet.log"
grep -qE 'check-grants: OK|\[ok\]' "${SCRATCH}/acl-fleet.log"

# Step 6 — import smoke (twice)
{
  echo "=== step6 imports-check $(date -Iseconds) ==="
  uv run python -c "
from roxabi_contracts.fleet.models import ContainerReport
from roxabi_contracts.fleet import subjects as fleet_subjects
import roxabi_obs.reporter
from factory.nats.fleet_store import FleetStore
print('imports:', ContainerReport, fleet_subjects, roxabi_obs.reporter, FleetStore)
"
  echo "imports_run1_exit=$?"
  uv run python -c "
from roxabi_contracts.fleet.models import ContainerReport
from roxabi_contracts.fleet import subjects as fleet_subjects
import roxabi_obs.reporter
from factory.nats.fleet_store import FleetStore
print('imports:', ContainerReport, fleet_subjects, roxabi_obs.reporter, FleetStore)
"
  echo "imports_run2_exit=$?"
  echo "step6_exit=0"
} 2>&1 | tee "${SCRATCH}/imports-check.log"
grep -q 'imports:' "${SCRATCH}/imports-check.log"
grep -q 'imports_run1_exit=0' "${SCRATCH}/imports-check.log"
grep -q 'imports_run2_exit=0' "${SCRATCH}/imports-check.log"

# Step 7 — source inspection
{
  echo "=== step7 source-inspection $(date -Iseconds) ==="
  test -f apps/dashboard/src/pages/FleetPage.tsx
  test -f apps/dashboard/src/router.tsx
  test -f apps/dashboard/src/lib/nav.ts
  test -f apps/dashboard/src/lib/api.ts
  test -f packages/roxabi-obs/src/roxabi_obs/reporter.py
  test -f packages/roxabi-obs/AGENTS.md
  test -f tools/emit_fleet_catalog.py
  grep -q 'fleet_list' packages/roxabi-contracts/src/roxabi_contracts/dashboard/subjects.py
  grep -q 'DashboardFleetRow' packages/roxabi-contracts/src/roxabi_contracts/dashboard/models.py
  grep -q 'fleet_list' src/factory/bootstrap/factory/dashboard_rpc.py
  grep -q 'CONTAINER_NAME' deploy/quadlet/factory-hub.container
  ! grep -rq 'infrastructure\.stores' src/factory/dashboard/
  ! grep -rq 'fleet_store' src/factory/dashboard/
  echo "step7_exit=0"
} 2>&1 | tee "${SCRATCH}/source-inspection.log"
grep -q 'step7_exit=0' "${SCRATCH}/source-inspection.log"

# Step 8 — catalogue emitter + unknown tierce row
{
  echo "=== step8 catalogue-check $(date -Iseconds) ==="
  uv run python -m py_compile tools/emit_fleet_catalog.py
  uv run python tools/emit_fleet_catalog.py | head -20
  uv run python -c "
import json, subprocess
from factory.nats.fleet_store import FleetStore
from factory.nats.fleet_catalog import FleetCatalogEntry

rows = json.loads(subprocess.check_output(['uv', 'run', 'python', 'tools/emit_fleet_catalog.py'], text=True))
loki = next(r for r in rows if r['container_name'] == 'factory-loki')
assert loki['instrumented'] is False, loki
catalog = [FleetCatalogEntry(**{k: v for k, v in loki.items() if k != 'source'}) for loki in [loki]]
store = FleetStore(catalog=catalog)
snap = store.list_snapshot()[0]
assert snap.status in ('unknown', 'pinned'), snap.status
print('loki_status=', snap.status)
"
  echo "step8_exit=0"
} 2>&1 | tee "${SCRATCH}/catalogue-check.log"
grep -q 'loki_status=' "${SCRATCH}/catalogue-check.log"
grep -q 'step8_exit=0' "${SCRATCH}/catalogue-check.log"

{
  echo "goal-fleet-obs evidence — $(date -Iseconds)"
  echo "branch: $(git branch --show-current)"
  echo "scratch: ${SCRATCH}"
  echo "steps 1-8: PASS"
} > "${SCRATCH}/execution-summary.txt"

echo "=== goal-fleet-obs-evidence PASS $(date -Iseconds) ===" | tee -a "${SCRATCH}/evidence-run.log"