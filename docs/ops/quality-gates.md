# Quality Gates — Index

Operator and contributor reference for every automated check that can block a commit, push, or merge. This page is the **wiring index**; gate definitions live in `.claude/stack.yml` (`quality_gates` block).

For script behaviour, exemption files, and exit-code contract, see [`tools/CLAUDE.md`](../../tools/CLAUDE.md).

---

## Source of truth

| Layer | Role |
|---|---|
| `.claude/stack.yml` → `quality_gates` | Canonical list of named gates, scripts, and intended stage (`pre-commit` default, or `pre-push`) |
| `tools/qg.conf` | Generated runtime config (file-length caps, globs). Drift-gated by `scripts/check-qg-conf-drift.sh` |
| `.pre-commit-config.yaml` | Local hooks on `git commit` and `git push` |
| `.github/workflows/ci.yml` | Merge-blocking CI on `main` / `staging` PRs |
| Path-triggered workflows | Additional gates when specific trees change (see below) |

**Stage vs wiring:** `stage: pre-push` in `stack.yml` means the gate is *intended* for the push hook. Some gates are CI-only (no pre-commit entry) or pre-push-only (declared in `stack.yml` but not yet wired — see [Gaps](#gaps)).

---

## Stages

| Stage | When it runs | How to invoke locally |
|---|---|---|
| **pre-commit** | `git commit` (default hook stage) | `pre-commit run --all-files` or `pre-commit run <hook-id>` |
| **pre-push** | `git push` | `pre-commit run --hook-stage pre-push --all-files` |
| **CI** | Every PR / push to `main` or `staging` | Push branch and watch Actions, or run the step command from `ci.yml` |
| **path-triggered** | PR touches listed paths | Push a branch with those paths changed |

---

## Gates in `stack.yml`

| Gate | Script / mechanism | `stack.yml` stage | pre-commit | pre-push | CI |
|---|---|:---:|:---:|:---:|:---:|
| `file_length` | `tools/check_file_length.sh` (SLOC via `radon`) | pre-commit | ✓ factory + package variants | — | ✓ |
| `folder_size` | `tools/check_folder_size.sh` | pre-commit | ✓ | — | — |
| `duplicate_test_basenames` | `tools/check_duplicate_test_basenames.sh` | pre-commit | ✓ | — | ✓ |
| `import_layers` | `uv run lint-imports` (`.importlinter`) | pre-push | ✓ (commit stage) | — | ✓ |
| `doc_drift` | `tools/check_doc_drift.py` | pre-commit* | — | — | ✓ |
| `doc_semantic_drift` | `tools/check_doc_semantic_drift.py` | pre-commit* | — | — | ✓ |
| `no_runtime_toml_bots` | `tools/check_no_runtime_toml_bots.sh` | pre-commit | — | ✓ | — |
| `architecture_snapshot` | `tools/check_architecture_snapshot.sh` | pre-push | — | ✓ | ✓ |
| `debt_expiry` | `tools/check_debt_expiry.sh` | pre-push | — | ✓ | ✓ |
| `hardcoded_constants` | `tools/check_hardcoded_constants.sh` | pre-commit | ✓ | — | ✓ |
| `file_exemptions` | `tools/check_file_exemptions.sh` | pre-commit | ✓ | — | ✓ |
| `test_sleep` | `tools/check_test_sleep.sh` | pre-push | — | ✓ | ✓ |
| `secrets_drift` | `tools/check_secrets_drift.sh` | pre-push | — | — | ✓ |
| `volumes_table` | `tools/check_volumes_table.sh` | pre-push | — | — | — |
| `quadlet_manifest_install` | `tools/check_quadlet_manifest_install.sh` | pre-push | — | — | ✓ |
| `secrets_source` | `tools/check_secrets_source.sh` | pre-push | — | — | — |
| `str_exc_bus_bound` | `tools/check_str_exc_bus_bound.sh` | pre-commit | — | — | ✓ |
| `lint_js` | `bun run lint` (Biome) | pre-commit | ✓ (path-filtered) | — | ✓ |
| `dashboard_unit_test` | `bun run --filter @roxabi-factory/dashboard test` | pre-push | — | ✓ (path-filtered) | ✓ |
| `dashboard_build` | `bun run build:dashboard` | — (CI/manual) | — | — | ✓ |

\* `doc_drift` and `doc_semantic_drift` have no explicit `stage` in `stack.yml` but are **CI-only** in practice (not listed in `.pre-commit-config.yaml`).

### Frontend gates (detail)

Declared under `frontend:` and `quality_gates` in `.claude/stack.yml` (#1771). Requires [bun](https://bun.sh) on `PATH` (`packageManager` in root `package.json`).

**`lint_js`** — `biome check` on `apps/`, `packages/`, `brand/`. Runs on **pre-commit** when staged files match `^(apps/|packages/shared/|brand/|biome.json|package.json|bun.lock)$`. Fix locally: `bun run format`.

**`dashboard_unit_test`** — Vitest for `apps/dashboard`. Runs on **pre-push** when `apps/dashboard/`, `packages/shared/`, or `brand/` changed.

**`dashboard_build`** — production SPA build. **CI-only** (and `make qg`); intentionally not a git hook — too slow for every push.

### Doc gates (detail)

**`doc_drift`** — allowlist scan: dead symbol/path references in operational docs (`docs/architecture/**` non-ADR, `docs/standards/**`, `CONFIGURATION.md`, `DEPLOYMENT.md`, runbooks, ops, CLAUDE.md network). Onboarding/narrative docs are exempt by omission. See `_collect_scan_files()` in `tools/check_doc_drift.py`.

**`doc_semantic_drift`** — regex scan for rename drift `check_doc_drift.py` misses: `make lyra`, `lyra config`, `~/.lyra`, `LYRA_HEALTH_*`, wrong container counts, README licence vs `pyproject.toml`. Scans `README.md`, `docs/**` (excl. `docs/history/**`, `docs/architecture/adr/**`), `deploy/CLAUDE.md`. Per-line exempt: `<!-- semantic-ignore -->`.

CI also greps operator docs for removed legacy logging configuration symbols (see the doc-drift step in `.github/workflows/ci.yml`) before running the Python gates.

---

## pre-commit hooks (not all in `stack.yml`)

| Hook ID | Entry | Stage |
|---|---|---|
| `lint` | `uv run ruff check .` | pre-commit |
| `typecheck` | `uv run pyright` | pre-commit |
| `codes-sync` | `check_codes_sync.py --write` (path-filtered) | pre-commit |
| `lint-js` | `bun run lint` (Biome; path-filtered) | pre-commit |
| `dashboard-unit-test` | `bun run --filter @roxabi-factory/dashboard test` (path-filtered) | pre-push |
| `trufflehog` | TruffleHog git scan | pre-push |
| `check-single-write-tool-display-config` | `tools/check_single_write_tool_display_config.sh` | pre-push |
| `license` | `tools/license_check.py` | pre-push |
| `qg-conf-drift` | `scripts/check-qg-conf-drift.sh` | pre-push |
| `acl-specs-drift` | `scripts/check-acl-specs-drift.sh` | pre-push |
| `acl-authconf-drift` | `scripts/check-acl-authconf-drift.sh` | pre-push |

Standard upstream hooks (`trailing-whitespace`, `check-yaml`, `check-toml`, `check-merge-conflict`, `check-added-large-files`) also run on commit.

---

## CI-only gates (not in `stack.yml`)

These run in `.github/workflows/ci.yml` but are not enumerated under `quality_gates`:

| Check | Command | Purpose |
|---|---|---|
| qg.conf drift | `scripts/check-qg-conf-drift.sh` | `tools/qg.conf` matches `stack.yml` |
| ACL retired identities | `uv run factory-check-acl-retired` | No live code references retired ACL identities |
| Request-reply flows | `uv run factory-check-flows` | Publish/subscribe/inbox grants cover declared flows |
| ACL grant coverage | `uv run factory-acl check grants --matrix deploy/nats/acl-matrix.json` | Code grants ⊆ matrix (ADR-079) |
| auth.conf drift | `scripts/check-acl-authconf-drift.sh` | Rendered `auth.conf` matches `acl-matrix.json` |
| ACL spec/fixture drift | `scripts/check-acl-specs-drift.sh` | Test fixtures match matrix |
| inbox_prefix API | `scripts/check_inbox_prefix.py` | Inbox subjects use `identity_name` API |
| Subject literals | `scripts/check_subject_literals.py` | Subject strings resolve via matrix + contracts |
| Error codes sync | `packages/roxabi-contracts/scripts/check_codes_sync.py` | `error-codes.md` matches `errors.py` |
| Hardcoded-constants self-test | `tests/tools/test_check_hardcoded_constants.sh` | Gate script regression |
| Coverage — factory | `pytest --cov-fail-under=50` | Minimum line coverage |
| Coverage — roxabi_nats | `pytest --cov-fail-under=70` | Package coverage |
| Coverage — roxabi_contracts | `pytest --cov-fail-under=80` | Package coverage |
| Integration tests | `pytest tests/integration/` | Docker-compose NATS matrix (`typing_enabled` true/false) |

---

## Path-triggered workflows

| Workflow | Triggers on | Gate |
|---|---|---|
| [`quadlet-lint.yml`](../../.github/workflows/quadlet-lint.yml) | `deploy/quadlet/**` | Podman Quadlet dry-run parse; inline `#` on value lines (#1083); `tools/check_quadlet_template_purity.sh` |
| [`renderer-roundtrip.yml`](../../.github/workflows/renderer-roundtrip.yml) | `factory-acl/**`, `deploy/nats/**`, roundtrip script | `tools/check_renderer_roundtrip.sh` per renderer (factory-acl, nats-conf, gen-certs, self-test) |
| [`secret-scan.yml`](../../.github/workflows/secret-scan.yml) | all PRs / protected pushes | TruffleHog `--only-verified` |
| [`pr-title.yml`](../../.github/workflows/pr-title.yml) | all PRs | Conventional Commits title |
| [`omp-base.yml`](../../.github/workflows/omp-base.yml) | `deploy/omp-base/**` | Containerfile pin parse + smoke build + version grep |

Local equivalent for Quadlet: `make quadlet-lint`.

---

## Review / policy workflows (non-blocking or advisory)

| Workflow | Role |
|---|---|
| [`axial-review.yml`](../../.github/workflows/axial-review.yml) | Auto-labels cross-layer PRs (`dev-core:axial-adr-review`, `dev-core:security-auditor`) — policy gate for merge review, not a script exit code |
| [`quality-debt.yml`](../../.github/workflows/quality-debt.yml) | Runs `make quality-debt-report` with `continue-on-error: true` — observability only |
| [`auto-merge.yml`](../../.github/workflows/auto-merge.yml) | Merge automation when CI + `reviewed` label green |

---

## Reporters (always exit 0 — not gates)

| Tool | Role |
|---|---|
| `tools/audit_quality_debt.py` | Suppression inventory → `artifacts/quality-debt-report.json` |
| `tools/classify_quality_debt.py` | Dry-run debt classification |
| `tools/license_check.py` | Licence scan (also runs as pre-push hook) |
| `tools/adr_consolidate.py` | ADR migration helper |
| `tools/capture_v1_text_baseline.py` | One-off baseline capture |

See [`docs/playbooks/quality-debt-pipeline.md`](../playbooks/quality-debt-pipeline.md) for the debt reporter playbook.

---

## Run locally (common)

```bash
# Full pre-commit suite
pre-commit run --all-files

# Pre-push hooks only
pre-commit run --hook-stage pre-push --all-files

# Doc gates (CI parity)
uv run python tools/check_doc_drift.py
uv run python tools/check_doc_semantic_drift.py

# Deploy gates declared pre-push but not in hooks (run before M₁ deploy PRs)
bash tools/check_volumes_table.sh
bash tools/check_secrets_source.sh   # skips gracefully when ~/.roxabi/factory absent

# Quadlet (path workflow parity)
make quadlet-lint
```

Fix `qg.conf` drift: `/release-setup --force` (or hand-sync then commit).

---

## Gaps

Gates declared in `stack.yml` with `stage: pre-push` but **not** wired to pre-commit or CI today:

| Gate | Manual command | When to run |
|---|---|---|
| `volumes_table` | `bash tools/check_volumes_table.sh` | Any PR touching `deploy/quadlet/**` volumes or `docs/architecture/deployment.md` Volumes table |
| `secrets_source` | `bash tools/check_secrets_source.sh` | Host deploy PRs; requires `~/.roxabi/factory` data dir (skips on CI runners) |

`str_exc_bus_bound` is in `stack.yml` as pre-commit-default but only runs in **CI** — run manually before push if you touch bus-bound error paths.

---

## Adding a gate

1. Add entry under `quality_gates` in `.claude/stack.yml` (script path, `stage`, description).
2. Regenerate `tools/qg.conf` via `/release-setup --force` if the gate uses shared config.
3. Wire to `.pre-commit-config.yaml` and/or `.github/workflows/ci.yml` (or a path-triggered workflow).
4. Document behaviour in `tools/CLAUDE.md` if non-obvious.
5. Add tests under `tests/tools/` when the gate has non-trivial logic.

New config renderers under `deploy/` must also register in `tools/check_renderer_roundtrip.sh` + `renderer-roundtrip.yml` ([`CONTRIBUTING.md`](../../CONTRIBUTING.md#adding-a-config-renderer)).