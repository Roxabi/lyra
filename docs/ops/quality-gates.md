# Quality Gates — Index

Every automated check that can block a commit, push, or merge — and where it runs.

Gate definitions: `.claude/stack.yml` → `quality_gates`. Wiring is verified by `tools/audit_gate_wiring.py` (CI).

Script behaviour: [`tools/CLAUDE.md`](../../tools/CLAUDE.md).

---

## Four layers (not one file)

| Layer | Config | When | Blocks merge? |
|---|---|---|---|
| **CI** | `.github/workflows/ci.yml` + path workflows | Every PR | Yes |
| **Git commit** | `.pre-commit-config.yaml` (default stage) | `git commit` | Local |
| **Git pre-push** | `.pre-commit-config.yaml` (`stages: [pre-push]`) | `git push` | Local |
| **Claude session** | `dev-core` plugin `hooks/hooks.json` | Agent Edit/Write | No |

`hooks.tool: pre-commit` in `stack.yml` declares the git hook runner. Install: `make hooks-install`.

**Claude hooks are separate.** They run during an agent session (format + security patterns on file edits). They do not replace git hooks or CI. Source: `roxabi-plugins/plugins/dev-core/hooks/` — see [Claude session hooks](#claude-session-hooks).

---

## Stages

| Stage | Invoke locally |
|---|---|
| commit | `pre-commit run --all-files` |
| pre-push | `pre-commit run --hook-stage pre-push --all-files` |
| CI | Push branch; or run step commands from `ci.yml` |
| wiring audit | `uv run python tools/audit_gate_wiring.py` |

---

## Gates in `stack.yml`

| Gate | Script / mechanism | stack stage | pre-commit | pre-push | CI |
|---|---|:---:|:---:|:---:|:---:|
| `file_length` | `check_file_length.sh` (SLOC) | commit | ✓ (+ package variants) | — | ✓ |
| `folder_size` | `check_folder_size.sh` | commit | ✓ | — | ✓ |
| `duplicate_test_basenames` | `check_duplicate_test_basenames.sh` | commit | ✓ | — | ✓ |
| `import_layers` | `lint-imports` | pre-push* | ✓ (commit stage) | — | ✓ |
| `doc_drift` | `check_doc_drift.py` | commit | — | — | ✓ |
| `no_runtime_toml_bots` | `check_no_runtime_toml_bots.sh` | commit | — | ✓ | ✓ |
| `architecture_snapshot` | `check_architecture_snapshot.sh` | pre-push | — | ✓ | ✓ |
| `debt_expiry` | `check_debt_expiry.sh` | pre-push | — | ✓ | ✓ |
| `hardcoded_constants` | `check_hardcoded_constants.sh` | commit | ✓ | — | ✓ |
| `file_exemptions` | `check_file_exemptions.sh` | commit | ✓ | — | ✓ |
| `test_sleep` | `check_test_sleep.sh` | pre-push | — | ✓ | ✓ |
| `secrets_drift` | `check_secrets_drift.sh` | pre-push | — | — | ✓ |
| `secrets_source` | `check_secrets_source.sh` | pre-push | — | ✓ | ✓† |
| `volumes_table` | `check_volumes_table.sh` | pre-push | — | — | ✓ |
| `quadlet_manifest_install` | `check_quadlet_manifest_install.sh` | pre-push | — | — | ✓ |
| `str_exc_bus_bound` | `check_str_exc_bus_bound.sh` | commit | — | — | ✓ |

\* `import_layers` runs at commit stage in `.pre-commit-config.yaml` despite `stage: pre-push` in `stack.yml`.

† `secrets_source` exits 0 on CI runners when `~/.roxabi/factory` is absent; enforces on hosts with data dir.

**Wiring audit** — `tools/audit_gate_wiring.py` fails CI if any enabled gate above is missing from both pre-commit and `ci.yml`.

---

## pre-commit hooks (not in `stack.yml`)

| Hook | Stage |
|---|---|
| `lint` / `typecheck` | commit |
| `codes-sync` | commit (path-filtered) |
| `trufflehog` | pre-push (`--only-verified --fail`) |
| `license` | pre-push |
| `qg-conf-drift` / ACL drift scripts | pre-push |
| `check-single-write-tool-display-config` | pre-push |

`license_check` also runs in CI (not a `stack.yml` gate).

---

## CI-only checks (not in `stack.yml`)

| Check | Purpose |
|---|---|
| `audit_gate_wiring.py` | stack.yml gates ⊆ pre-commit ∪ CI |
| ACL retired / flows / grants | NATS matrix enforcement |
| `check_inbox_prefix.py` / `check_subject_literals.py` | Subject discipline |
| `check_codes_sync.py` | Error codes registry |
| Coverage floors (50 / 70 / 80 %) | factory / nats / contracts |
| Integration matrix | Docker NATS (`typing_enabled` true/false) |

Path-triggered: `quadlet-lint.yml`, `renderer-roundtrip.yml`, `secret-scan.yml`, `pr-title.yml`, `omp-base.yml`.

---

## Claude session hooks

From plugin `dev-core@roxabi-marketplace` (not git, not CI):

| Hook | Trigger | Blocking? | Action |
|---|---|:---:|---|
| `format.js` | PostToolUse Edit/Write | No | Runs `build.formatter_fix_cmd` from `stack.yml` (`ruff format` + `ruff check --fix`) |
| `security-check.js` | PreToolUse Edit/Write | Yes | Blocks naive hardcoded secrets / injection patterns |
| bun test blocker | PreToolUse Bash | Yes | N/A for this Python repo |

Override per project: `.claude/hooks/hooks.json`. Full reference: `roxabi-plugins/plugins/dev-core/hooks/README.md`.

---

## Run locally

```bash
make hooks-install
pre-commit run --all-files
pre-commit run --hook-stage pre-push --all-files
uv run python tools/audit_gate_wiring.py
```

---

## Adding a gate

1. Add under `quality_gates` in `.claude/stack.yml`.
2. Wire to `.pre-commit-config.yaml` and/or `.github/workflows/ci.yml`.
3. `uv run python tools/audit_gate_wiring.py` must pass.
4. Update this file if the gate is non-obvious.