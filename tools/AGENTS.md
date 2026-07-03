# AGENTS.md — tools/

## Role

Quality gates and analysis scripts for the Factory codebase.
Canonical source: `roxabi-plugins/plugins/dev-core/tools/` — ¬edit project-side copies directly.

**vs `scripts/`:** `tools/` = gate implementations invoked by `scripts/qg run` from `stack.yml`. `scripts/` = platform orchestration (`qg` runner), drift wrappers, and repo-specific ACL/deploy scanners called directly from CI/Makefile. See `scripts/AGENTS.md` and `CONTRIBUTING.md` § Language & layout.

**Languages:** gate shell entrypoints are bash; parsing-heavy gates may use Python. Orchestration (`scripts/qg`) is bash + yq only.

**Deploy gates in `tools/`:** `check_secrets_drift.sh`, `check_quadlet_manifest_install.sh`, `check_volumes_table.sh`, `check_secrets_source.sh` — pre-push, ci, and `profiles.local` (see `docs/ops/quality-gates.md` § Deploy gates). `check_quadlet_component_source.sh` is **ci-only**.

## Wiring

Gates are declared in `.claude/stack.yml` (`quality_gates` + `qg.run_order`) and executed by `scripts/qg` (bash) — no generated pre-commit/CI wiring. Index: `docs/ops/quality-gates.md`.

`tools/qg.conf` is generated from `stack.yml` file-length settings; drift-gated by `scripts/check-qg-conf-drift.sh`. Fix via `/release-setup --force`.

### `check_doc_semantic_drift.py` — living-doc patterns (Phase C)

Regex gate for operator-facing stale text that `check_doc_drift.py` misses: `make lyra`, `lyra config`, `~/.lyra`, wrong container counts, README licence vs `pyproject.toml`. Scans `README.md`, `docs/**` (excl. `docs/history/**`, `docs/architecture/adr/**`), `deploy/CLAUDE.md`. Per-line exempt: `<!-- semantic-ignore -->`, historical keywords.

### `check_doc_drift.py` — scanned scope (allowlist, #1538)

`_collect_scan_files()` is an **allowlist** — only listed paths are drift-gated. New top-level narrative docs are exempt by omission (no action needed).

| Scanned (operational truth — cite live symbols/paths) | Exempt (narrative/onboarding/aspirational — illustrative/future refs by design) |
|---|---|
| `docs/architecture/**` (non-`adr/`), `docs/ARCHITECTURE.md` | `docs/architecture/adr/**` (immutable records, ADR-080) |
| `docs/standards/**` | `docs/HAPPY-PATHS.md`, `COMMANDS.md` |
| `docs/CONFIGURATION.md`, `DEPLOYMENT.md`, `docs/QUICKSTART.md`, `GETTING-STARTED.md`, `MULTI-BOT.md` (onboarding, #2201) | `docs/OBSERVABILITY.md`, `ROADMAP.md`, `vision.md` |
| `docs/agent-management.md`, `bot-management.md`, `data-dirs.md`, `debt-tracking.md` (#2200) | — |
| `docs/ops/**`, `docs/runbooks/**`, `docs/playbooks/**` | `docs/history/**`, `artifacts/**` |
| AGENTS.md network (root, `src/`, `packages/`, `plugins/`) | — |

Add a doc to the gate → list it (or its dir) in `_collect_scan_files()`; regenerate via `--update-baseline` (new dead refs join the #1536 burn-down).

`file_length` runs in **SLOC mode** (`QG_FILE_METRIC=sloc`, `metric: sloc` in `stack.yml`): the cap counts source lines only — blanks, comments and docstrings excluded — via `radon` (a dev dep; Node repos would use `npx sloc`). `check_file_length.sh` sources `check_lib.sh` for the exemption helpers. Exemption counts (`# N lines`) are SLOC too. Switching back to raw `wc -l` = set `metric: raw` (or drop the key) and re-run `/release-setup --force`.

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

Persistent gates are enumerated in `.claude/stack.yml` `quality_gates`. One-off analysis scripts (`audit_quality_debt.py`, `classify_quality_debt.py`, `capture_v1_text_baseline.py`, `license_check.py`) always exit 0 — they are reporters, not gates. Run `ls tools/*.py tools/*.sh` for the full listing.

`archive_artifacts_wave.py` is neither a gate nor a reporter — it is a manual/monthly **maintenance mutation** tool (policy: `artifacts/README.md`). It moves closed-issue `artifacts/` deltas to `artifacts/archive/YYYY-MM/` and, unlike the read-only scanners above, its exit code IS load-bearing: `0` clean, `1` a link would be stranded, `2` tool error. Its `--apply` rewrite pass deliberately does **not** exclude `tests/`/`packages/` (contrast the #1162 rule) — it rewrites an **exact** archived-path string, so a hit anywhere is a genuine reference that must move too, or the 0-broken-links bar fails.


## Scope

`tools/` = project-root tooling only. ¬confuse with `src/factory/tools/` (gh_token helper — unrelated).
