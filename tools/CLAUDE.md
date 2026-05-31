# CLAUDE.md — tools/

## Role

Quality gates and analysis scripts for the Lyra codebase.
Canonical source: `roxabi-plugins/plugins/dev-core/tools/` — ¬edit project-side copies directly.

## Wiring

Scripts are driven by `.claude/stack.yml` `quality_gates` block and pre-push hooks:

| Script | Gate | Stage |
|---|---|---|
| `check_file_length.sh` | `file_length` — 300-line cap on `src/**/*.py` | pre-commit |
| `check_folder_size.sh` | `folder_size` — 12-file cap per `src/**` folder | pre-commit |
| `check_duplicate_test_basenames.sh` | `duplicate_test_basenames` | pre-commit |
| `.importlinter` (external) | `import_layers` | pre-push |
| `check_doc_drift.py` | `doc_drift` — dead backtick refs in docs + CLAUDE.md network; baseline in `doc_drift_baseline.txt` (burn-down list, epic #1530) | CI |

### `check_doc_drift.py` — scanned scope (allowlist, #1538)

`_collect_scan_files()` is an **allowlist** — only listed paths are drift-gated. New top-level narrative docs are exempt by omission (no action needed).

| Scanned (operational truth — cite live symbols/paths) | Exempt (narrative/onboarding/aspirational — illustrative/future refs by design) |
|---|---|
| `docs/architecture/**` (non-`adr/`), `docs/ARCHITECTURE.md` | `docs/architecture/adr/**` (immutable records, ADR-080) |
| `docs/standards/**` | `docs/QUICKSTART.md`, `GETTING-STARTED.md`, `HAPPY-PATHS.md`, `COMMANDS.md` |
| `docs/CONFIGURATION.md`, `DEPLOYMENT.md`, `QUADLET-DEPLOYMENT.md` | `docs/MULTI-BOT.md`, `OBSERVABILITY.md`, `ROADMAP.md`, `vision.md` |
| `docs/agent-management.md`, `bot-management.md`, `data-dirs.md` | `docs/code-quality-exceptions.md`, `debt-tracking.md` |
| `docs/ops/**`, `docs/runbooks/**`, `docs/playbooks/**` | `docs/memory-system/**`, `docs/history/**`, `artifacts/**` |
| CLAUDE.md network (root, `src/`, `packages/`, `plugins/`) | — |

Add a doc to the gate → list it (or its dir) in `_collect_scan_files()`; regenerate via `--update-baseline` (new dead refs join the #1536 burn-down).

Runtime config: `tools/qg.conf` (seeded from `stack.yml` by `/release-setup`); scripts fall back to hardcoded defaults when absent.

## Exit-code contract (hard rule — #1162 hotfix)

Exit code = "script ran OK" vs "script broke" — NEVER "violations found".

- `exit 0` = ran cleanly (violations found or not — caller reads stdout/stderr)
- `exit 1` = violations found (gates use this to fail the hook)
- `exit 2+` = script itself broke (parse error, missing dep, etc.)

Corollary: `audit_quality_debt.py` always exits 0 — it is a reporter, not a gate.
¬wrap gate scripts with `set -e` in a parent script that also runs reporters.

## Exemption files

`file_exemptions.txt` and `folder_exemptions.txt` — paths that exceed the cap by design.

Format: `<path>  # <N> lines|files — DEBT:<slug> — <issue> <rationale>`

Rules:
- Every exemption MUST have a tracking issue or ADR reference.
- Declared local cap (`# N lines`) is enforced — the file may not grow past it silently.
- Omitting the count = back-compat full bypass (legacy entries only; ¬add new ones without a count).
- Paths ¬contain spaces (awk field-split would break exact matching).

## Read-only vs write tools

`audit_quality_debt.py`, `classify_quality_debt.py`, `capture_v1_text_baseline.py` — read-only scanners.
If a `--apply` or mutation flag is ever added: default scope MUST exclude `tests/`, `fixtures/`, `packages/`.
Read tools tolerate false positives; write tools must not mutate test/fixture files (#1162 lesson:
`--apply` on the classifier mutated `test_classifier.py`).

## One-off analyses vs persistent gates

| Persistent gates (run every push) | One-off analyses (run on demand) |
|---|---|
| `check_file_length.sh` | `adr_consolidate.py` |
| `check_folder_size.sh` | `audit_quality_debt.py` |
| `check_duplicate_test_basenames.sh` | `classify_quality_debt.py` |
| `check-nats-acls.sh` | `capture_v1_text_baseline.py` |
| `smoke_llm_e2e.sh` | `license_check.py` |
| `check_doc_drift.py` | — |

`adr_consolidate.py` — migration tool (flat ADR archive → domain pages); see `artifacts/analyses/adr-consolidation-matrix.md`.

## Scope

`tools/` = project-root tooling only. ¬confuse with `src/lyra/tools/` (gh_token helper — unrelated).
