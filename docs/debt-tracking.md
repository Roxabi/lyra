---
title: Debt Tracking
status: active
---

# Debt Tracking

Every suppression marker in `src/` that cannot be immediately refactored is tracked
as an honest debt entry. There is no approved escape-hatch and no quota — the audit
is visibility-only. The **one** enforcement gate is time: a `DEBT:` marker on a file
untouched for 6+ months with no issue reference blocks the push (`tools/check_debt_expiry.sh`).

---

## Grammar

Every `noqa`, `pyright: ignore`, or `type: ignore` marker in `src/` carries
`— DEBT:<slug>` after the rule code, separated by ` — ` (em-dash with spaces).

Slugs are kebab-case and must match a registry file in `artifacts/debt/`.

```python
# noqa: BLE001 — DEBT:boundary-broad-catch
# pyright: ignore[reportArgumentType] — DEBT:defensive-narrow-payloads
# type: ignore[assignment] — DEBT:defensive-narrow-payloads
```

Markers without a `DEBT:<slug>` suffix are flagged by `tools/audit_quality_debt.py`
(warning on stderr; the audit itself never fails a gate — see § Enforcement).

---

## Registry Conventions

Each referenced `<slug>` has a matching `artifacts/debt/<slug>.md` file. These
per-slug files are the source of truth.

**Frontmatter fields:**

| Field | Type | Required | Notes |
|---|---|---|---|
| `slug` | string | yes | matches filename (no `.md`) |
| `status` | `open` \| `drained` | yes | flip to `drained` when all callsites are gone |
| `created` | ISO date | yes | date the entry was first committed |
| `rules` | list | yes | ruff/pyright rule codes suppressed |
| `drain_slice` | string | no | epic or PR reference driving the drain |
| `parent_slice` | string | no | issue that first introduced this entry |
| `fix_class` | `small` \| `medium` \| `large` | no | effort estimate |

**Body sections:**

- `## Pattern` — what the suppressed pattern is and why the right fix is non-trivial
- `## Sites` — refers to `artifacts/quality-debt-report.json` for the live list
- `## Drain plan` — concrete refactor steps when the drain is scheduled
- `## Notes` — history, lifecycle events, related issues

`artifacts/debt/INDEX.md` is a hand-maintained convenience table. No tool populates
it — edit it in the same commit as the underlying registry file it summarises.

---

## Adding a DEBT Entry

Two steps, in order:

1. **Create or reuse** `artifacts/debt/<slug>.md` with `status: open` and the
   required frontmatter fields. Reuse an existing slug if the pattern is the same.
2. **Append `— DEBT:<slug>`** to the suppression marker at the callsite.

No quota, no PR-template requirement. The audit script warns on stderr but always
exits 0. The only way a `DEBT:` marker fails a gate is by going stale (§ Enforcement).

If you can eliminate the suppression entirely by refactoring, do that instead —
no entry needed.

---

## Local audit

```sh
make quality-debt-report
```

Runs `tools/audit_quality_debt.py`, which scans `src/` for tagged and untagged
markers, cross-references `artifacts/debt/`, and writes `artifacts/quality-debt-report.json`.

Output shape:

- `rows` — one entry per marker site: `file`, `line`, `rule`, `bucket`
  (`DEBT` | `UNTAGGED`), `slug` (if tagged)
- `stale_references` — `DEBT:<slug>` references in `src/` that point to a missing
  or `drained` registry file
- `counts_by_rule_bucket_slug` — summary counts

**stderr warnings:**

- `UNTAGGED: <file>:<line>` — marker has no `— DEBT:<slug>` suffix; add one or
  eliminate the suppression
- `STALE_REF: <slug>` — slug referenced in `src/` but `artifacts/debt/<slug>.md`
  is missing or `status: drained`

| State | Expected |
|---|---|
| Clean repo (all markers tagged, all slugs open) | zero warnings |
| Open DEBT entries present | `rows` count > 0; no warnings — this is normal |
| Untagged markers present | stderr warnings; non-zero `UNTAGGED` bucket count |

The audit is a reporter, not a gate (exit code always 0). It never blocks a push.

---

## Reviewing the live report

After running `make quality-debt-report`:

```sh
# Total marker count
jq '.rows | length' artifacts/quality-debt-report.json

# Distinct slugs currently in use
jq '[.rows[] | select(.bucket == "DEBT") | .slug] | unique' artifacts/quality-debt-report.json

# DEBT references pointing to missing or drained registry files
jq '.stale_references' artifacts/quality-debt-report.json

# All markers tagged with a specific slug across src/
grep -rn 'DEBT:boundary-broad-catch' src/
```

The `artifacts/quality-debt-report.json` output is the intended handoff point for a
future async debt-drain pipeline (separate epic, not tracked in this repo). Nothing
consumes it automatically today.

---

## Enforcement

Two checks touch `DEBT:` markers. Only the second one can fail a gate.

| Check | Gate | Stages | Semantics |
|---|---|---|---|
| `tools/audit_quality_debt.py` (`make quality-debt-report`) | — (`.github/workflows/quality-debt.yml`, `continue-on-error`) | informational | always exits 0; warns on untagged / stale-ref markers |
| `tools/check_debt_expiry.sh` | `debt_expiry` | **pre-push + ci** | **merge-blocking** (exit 1) on stale markers |

A `DEBT:` marker is **stale** when both hold:

1. Its file was last committed more than 6 months ago (`git log` file date).
2. The marker line carries **no** GitHub issue reference (`#NNN`).

An issue reference pins the marker (active remediation path); recent files are
assumed to have been reviewed. To keep a stale marker alive, do one of:

1. **Resolve the debt** and remove the marker.
2. **Open an issue** and append `#<N>` to the marker line — e.g.
   `# noqa: BLE001 — DEBT:boundary-broad-catch #1234`.
3. **Touch the file** in a meaningful commit — resets the 6-month clock.

Grace period and scan root are configurable via `DEBT_EXPIRY_MONTHS` (default: 6)
and `DEBT_SCAN_ROOT` (default: `src/`). See also README § Debt expiry policy.

---

## Lifecycle

When all callsites for a slug are refactored away, flip `status: open → drained`
in the registry file. Do **not** delete the file — it serves as re-introduction
detection history. A `DEBT:<slug>` marker against a `status: drained` entry is
flagged by the audit script (warning, not a gate failure).
