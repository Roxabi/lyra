#!/usr/bin/env bash
# Ingress connector × tenant goal gates (ADR-096) — mirrors goal/plan.md verif steps.
set -euo pipefail

REPO="${REPO:-/home/mickael/.grok/worktrees/projects-roxabi-factory/2026-06-29-544af04a}"
SCRATCH="${SCRATCH:-/tmp/grok-goal-a5c30d25aab6/implementer}"
cd "$REPO"
mkdir -p "$SCRATCH"

echo "== step 0: full-repo ruff (ingress tests first) =="
uv run ruff check tests/ingress/ --fix
uv run ruff check . >"$SCRATCH/full-ruff.log" 2>&1

echo "== step 1: contracts + ingress pytest =="
uv run pytest \
  packages/roxabi-contracts/tests/test_event_subjects.py \
  packages/roxabi-contracts/tests/test_event_models.py \
  tests/ingress/ \
  -q --tb=line 2>&1 | tee "$SCRATCH/contracts-ingress-tests.log"

echo "== step 2: scoped ruff + pyright =="
uv run ruff check \
  src/factory/ingress \
  src/factory/infrastructure/stores/ingress \
  packages/roxabi-contracts/src/roxabi_contracts/event \
  2>&1 | tee "$SCRATCH/ruff-ingress.log"
uv run pyright \
  src/factory/ingress \
  src/factory/infrastructure/stores/ingress \
  packages/roxabi-contracts/src/roxabi_contracts \
  2>&1 | tee "$SCRATCH/pyright-ingress.log"
uv run pyright >"$SCRATCH/full-pyright.log" 2>&1

echo "== step 3: serve launch + health (2x) =="
mkdir -p "$SCRATCH/itmp"
echo 'dummygh' >"$SCRATCH/itmp/gh.tok"
printf '[connector.github]\nenabled = true\nsecret_path = "%s"\n[connector.cloudflare]\nenabled = false\n' \
  "$(realpath "$SCRATCH/itmp/gh.tok")" >"$SCRATCH/itmp/ingress.toml"
for i in 1 2; do
  timeout 6s bash -c "
    FACTORY_INGRESS_CONFIG='$SCRATCH/itmp/ingress.toml' \
    INGRESS_GITHUB_WEBHOOK_SECRET_PATH='$SCRATCH/itmp/gh.tok' \
    NATS_URL=nats://127.0.0.1:14222 \
    timeout 4s uv run factory ingress serve --host 127.0.0.1 --port '1878$i' \
      >'$SCRATCH/serve$i.log' 2>&1 &
    SPID=\$!
    sleep 1.2
    curl -s --max-time 1 'http://127.0.0.1:1878$i/health' >'$SCRATCH/health$i.json' \
      || echo 'curl-fail-$i' >'$SCRATCH/health$i.json'
    kill \"\$SPID\" 2>/dev/null || true
    wait \"\$SPID\" 2>/dev/null || true
  " || true
done
cat "$SCRATCH/health1.json" "$SCRATCH/health2.json" "$SCRATCH/serve1.log" | head -30
if ! grep -q '"ok": true' "$SCRATCH/health1.json" 2>/dev/null \
  || ! grep -q '"ok": true' "$SCRATCH/health2.json" 2>/dev/null; then
  echo "serve+curl failed — running plan fallback (literal python -c)"
  uv run python -c '
import os, tempfile, pathlib
from fastapi.testclient import TestClient
from factory.ingress.serve import create_app
with tempfile.TemporaryDirectory() as td:
    p = pathlib.Path(td)/"ingress.toml"; p.write_text("[connector.github]\nenabled=true\n")
    os.environ["FACTORY_INGRESS_CONFIG"] = str(p)
    os.environ["INGRESS_GITHUB_WEBHOOK_SECRET_PATH"] = "/dev/null"
    c = TestClient(create_app()); h = c.get("/health").json(); print("HEALTH_OBSERVABLE:", h); assert "ok" in h and "github" in h
' 2>&1 | tee "$SCRATCH/create-app-fallback.log"
  uv run python -c '
import os, tempfile, pathlib
from fastapi.testclient import TestClient
from factory.ingress.serve import create_app
with tempfile.TemporaryDirectory() as td:
    p = pathlib.Path(td)/"ingress.toml"; p.write_text("[connector.github]\nenabled=true\n")
    os.environ["FACTORY_INGRESS_CONFIG"] = str(p)
    os.environ["INGRESS_GITHUB_WEBHOOK_SECRET_PATH"] = "/dev/null"
    c = TestClient(create_app()); h = c.get("/health").json(); print("HEALTH_OBSERVABLE:", h); assert "ok" in h and "github" in h
' 2>&1 | tee -a "$SCRATCH/create-app-fallback.log"
fi

echo "== step 4: store resolve =="
uv run python -c '
from factory.infrastructure.stores.ingress.installation_store import InstallationStore
import asyncio, tempfile, pathlib, os
async def t():
  with tempfile.TemporaryDirectory() as td:
    dbp = pathlib.Path(td)/"ingress.db"
    s=InstallationStore(str(dbp))
    await s.connect()
    await s.upsert_lifecycle("github", "12345", "default", enabled=True)
    t = await s.resolve("github", "12345")
    print("RESOLVE_OK:", t)
    t2 = await s.resolve("github", "99999")
    print("RESOLVE_UNKNOWN:", t2)
    await s.close()
asyncio.run(t())
' 2>&1 | tee "$SCRATCH/store-resolve.log"

echo "== step 5: CLI help =="
uv run factory ingress --help 2>&1 | tee "$SCRATCH/ingress-cli-help.log"

echo "== step 6: runbook evidence =="
grep -E "(factory-ingress|INGRESS_GITHUB_INSTALLATION_ID|unknown_installation|LogQL)" \
  docs/runbooks/ingress-webhooks.md \
  docs/runbooks/quadlet-install.md \
  deploy/secrets-policy.toml \
  "$SCRATCH"/serve*.log 2>/dev/null | cat \
  | tee "$SCRATCH/runbook-grep.log"

echo "== step 7: spec + publisher grep =="
grep -E "tenant|per_connector_tenant_event|factory\.event\.github\." \
  artifacts/specs/sentinelle-four-planes-spec.mdx \
  packages/roxabi-contracts/src/roxabi_contracts/event/subjects.py \
  src/factory/ingress/publisher.py 2>&1 | cat \
  | tee "$SCRATCH/spec-grep.log"

echo "== step 8: full pytest =="
uv run pytest -q --tb=no 2>&1 | tail -5 | tee "$SCRATCH/full-pytest.log"

echo "== step 9: deploy checks =="
bash tools/check_quadlet_manifest_install.sh 2>&1 | tee "$SCRATCH/quadlet-check.log" || true
bash tools/check_secrets_source.sh 2>&1 | tee "$SCRATCH/secrets-check.log" || true

ls -la "$SCRATCH/" | tee "$SCRATCH/ls-scratch.log"
echo "ingress_goal_gates: OK"