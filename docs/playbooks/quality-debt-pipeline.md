---
title: Quality-Debt Pipeline — Operational Playbook
description: Operator how-to for the quality-debt observability layer — running audits, adding DEBT entries, and reading the live report.
---

# Quality-Debt Pipeline — Operational Playbook

Operational steps for working with the quality-debt observability layer.
Grammar, registry schema, and workflow reference live in `docs/debt-tracking.md` — this playbook does not duplicate them.

This pipeline is informational only. No enforcement gates. Every check exits 0.

---

## §1 Local audit

### Running the audit

```sh
make quality-debt-report
```

Invokes `tools/audit_quality_debt.py`, scans `src/` for suppression markers, cross-references `artifacts/debt/`, and writes `artifacts/quality-debt-report.json`.

Output shape:

- `rows` — one entry per marker site: `file`, `line`, `rule`, `bucket` (`DEBT` | `UNTAGGED`), `slug` (if tagged)
- `stale_references` — `DEBT:<slug>` references in `src/` that point to a missing or `drained` registry file
- `summary` — counts by bucket

### Interpreting stderr warnings

- `UNTAGGED: <file>:<line>` — marker has no `— DEBT:<slug>` suffix; add one or eliminate the suppression
- `STALE_REF: <slug>` — slug referenced in `src/` but `artifacts/debt/<slug>.md` is missing or `status: drained`

### Expected vs. non-zero warnings

| State | Expected |
|---|---|
| Clean repo (all markers tagged, all slugs open) | zero warnings |
| Open DEBT entries present | `rows` count > 0; no warnings — this is normal |
| Untagged markers present | stderr warnings; non-zero `UNTAGGED` bucket count |

---

## §2 Adding a new DEBT entry

Two steps — see `docs/debt-tracking.md` "Adding a DEBT entry" for the canonical reference.

1. Create or reuse `artifacts/debt/<slug>.md` with `status: open` and required frontmatter.
2. Append `— DEBT:<slug>` to the suppression marker at the callsite.

Example:

```python
except Exception as e:  # noqa: BLE001 — DEBT:boundary-broad-catch
```

---

## §3 Reviewing the live debt report

After running `make quality-debt-report`:

```sh
# Total marker count
cat artifacts/quality-debt-report.json | jq '.rows | length'

# Distinct slugs currently in use
cat artifacts/quality-debt-report.json | jq '[.rows[] | select(.bucket == "DEBT") | .slug] | unique'

# DEBT references pointing to missing or drained registry files
cat artifacts/quality-debt-report.json | jq '.stale_references'

# All markers tagged with a specific slug across src/
grep -rn 'DEBT:boundary-broad-catch' src/
```

---

## §4 When the async drain pipeline lands

This playbook will gain a section on async drain triggers when that future epic is built.
For now, `artifacts/debt/<slug>.md` is an input queue — nothing consumes it automatically.
The `artifacts/quality-debt-report.json` is the intended handoff point.

---

## §5 Migrating legacy POLICY markers

During #1175, 190 `POLICY:<tag>` markers from the original tagging pass were converted to `DEBT:<slug>`. The command used:

```sh
uv run python tools/classify_quality_debt.py --migrate-policy --root .
```

This should not need to run again. If `POLICY:` markers re-appear they will be flagged as `UNTAGGED` by the audit — treat them the same as any other untagged marker.

---

## §6 CI and pre-push behaviour

- **CI**: `.github/workflows/quality-debt.yml` runs `make quality-debt-report` on every PR — informational only (`continue-on-error: true`). Failures do not block merge.
- **Pre-push**: no quality-debt hook with failure semantics. Run `make quality-debt-report` locally at any time; it never blocks a push.
