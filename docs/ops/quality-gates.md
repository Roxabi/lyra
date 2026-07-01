# Quality Gates — Index

Operator reference for automated checks. **Single source of truth:** `.claude/stack.yml` (`quality_gates` + `qg.run_order`).

**Runner:** `scripts/qg` (bash + [yq](https://github.com/mikefarah/yq)) — reads stack.yml at execution time (no generated wiring, no drift).

**Layout:** gate implementations live in `tools/`; `scripts/` holds the runner and repo-specific CI extras. See `CONTRIBUTING.md` § Language & layout.

For script behaviour and exit codes, see [`tools/CLAUDE.md`](../../tools/CLAUDE.md).

---

## Architecture (4 files)

| File | Role |
|------|------|
| `.claude/stack.yml` | Declares gates (`quality_gates`), stages, scripts, file filters, execution order (`qg.run_order`) |
| `scripts/qg` | Executes gates for a stage, profile, or single gate name |
| `.pre-commit-config.yaml` | Stable shell: upstream hooks + `qg run --stage pre-commit` / `pre-push` |
| `.github/workflows/ci.yml` | CI bootstrap (nats, uv, bun, yq) + `qg run --stage ci` + meta-tests, coverage, e2e |

`tools/qg.conf` remains generated runtime config for file-length scripts (drift-gated by `scripts/check-qg-conf-drift.sh`).

---

## Run locally

```bash
make dev-setup                              # after clone
scripts/qg run --stage pre-commit           # commit hooks parity
scripts/qg run --stage pre-push             # push hooks parity
scripts/qg run --stage ci                   # CI gate bundle
scripts/qg run lint_js                      # single gate
make qg                                     # profile local + extra factory tests
pre-commit run --all-files                  # upstream + pre-commit stage
pre-commit run --hook-stage pre-push --all-files
```

---

## Stages

| Stage | When | stack.yml key |
|-------|------|---------------|
| `pre-commit` | `git commit` | `qg.run_order.pre-commit` |
| `pre-push` | `git push` | `qg.run_order.pre-push` |
| `ci` | GitHub Actions `ci` job | `qg.run_order.ci` |

Gates list target stages in `quality_gates.<name>.stages`.

Path-filtered gates (`files:` regex) skip when no changed file matches (commit/push hooks only).

---

## CI extras (not in `qg run --stage ci`)

Explicit steps in `.github/workflows/ci.yml` after the QG bundle:

- Gate self-tests (`tests/tools/test_check_*.sh`, `tests/scripts/test_qg.sh`)
- Dashboard Playwright e2e, package coverage thresholds
- Jobs `integration`, `docker-build`

ACL scanners (`acl_matrix_retired`, `request_reply_flows`, `acl_grants`, `inbox_prefix`, `subject_literals`) are declared in `stack.yml` and run inside `qg run --stage ci`.

---

## Adding a gate

1. Add `quality_gates.<name>` in `.claude/stack.yml` (`script`, `stages`, optional `files`, `env`, `requires`).
2. Add `<name>` to `qg.run_order.<stage>` for each stage it should run in.
3. Regenerate `tools/qg.conf` via `/release-setup --force` if the gate uses file-length/folder shared config.
4. Document non-obvious behaviour in `tools/CLAUDE.md`; add `tests/tools/` when logic is non-trivial.

No pre-commit or ci.yml edit required for standard gates.

---

## Gaps — deploy gates easy to miss locally

Some gates run on **`git push`** (pre-push stage) but not when you only run `--stage pre-commit`, `--stage ci`, or `make qg` (local profile):

| Gate | pre-push | ci | `make qg` |
|------|:--------:|:--:|:---------:|
| `secrets_source` | yes | no | no |
| `volumes_table` | yes | yes | no |

Before a **deploy PR**, run:

```bash
scripts/qg run --stage pre-push    # includes secrets_source + volumes_table
# or, deploy-only subset:
scripts/qg run secrets_source
scripts/qg run volumes_table
```

`make quadlet-lint` for path-scoped Quadlet checks (see `quadlet-lint.yml` workflow).