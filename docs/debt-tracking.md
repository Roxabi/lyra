---
title: Debt Tracking
status: active
---

# Debt Tracking

Every suppression marker in `src/` that cannot be immediately refactored is tracked
as an honest debt entry. There are no approved escape-hatches, no enforcement gates,
and no quota — only visibility.

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
(warning on stderr; never a gate failure).

---

## Registry Conventions

Each referenced `<slug>` has a matching `artifacts/debt/<slug>.md` file.

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

`artifacts/debt/INDEX.md` is updated by `make quality-debt-report`. Do not hand-edit
the row content; update the underlying registry file instead.

---

## Adding a DEBT Entry

Two steps, in order:

1. **Create or reuse** `artifacts/debt/<slug>.md` with `status: open` and the
   required frontmatter fields. Reuse an existing slug if the pattern is the same.
2. **Append `— DEBT:<slug>`** to the suppression marker at the callsite.

No enforcement gate, no PR template requirement, no quota. The audit script warns
on stderr but always exits 0.

If you can eliminate the suppression entirely by refactoring, do that instead —
no entry needed.

---

## Workflow

```
make quality-debt-report
```

Runs `tools/audit_quality_debt.py`, which scans `src/` for tagged and untagged
markers, cross-references `artifacts/debt/`, and emits `artifacts/quality-debt-report.json`.

Pre-push and CI run the same audit in informational mode: warnings appear on stderr,
exit code is always 0. No push is blocked by this check.

The `artifacts/quality-debt-report.json` output is the input queue for the future
async debt-drain pipeline (separate epic, not tracked in this repo). When that
pipeline is ready it will read the JSON to schedule drain passes per slug.

---

## Lifecycle

When all callsites for a slug are refactored away, flip `status: open → drained`
in the registry file. Do **not** delete the file — it serves as re-introduction
detection history. A `DEBT:<slug>` marker against a `status: drained` entry is
flagged by the audit script (warning, not a gate failure).
