# `artifacts/` — retention & archive policy

Point-in-time outputs of the `/dev` lifecycle and other agent workflows. Per the
retrieval ladder in [ADR-086](../docs/architecture/adr/086-documentation-architecture.mdx)
(§ Retrieval ladder, step 5), everything here is **DELTAS only — never
current-state**. Current architectural truth lives in `docs/architecture/`
domain pages (Tier 2) and the generated Tier-1 snapshot; this tree records *how
we got there*, one issue at a time.

This file is the SSoT for what stays, what ages out, how the archive wave runs,
and how a wave is **tombstoned** (purged from `HEAD`, recoverable via git tag —
see § Tombstoned waves).

## Layout

| Path | Contents | Lifecycle |
|------|----------|-----------|
| `frames/`, `analyses/`, `specs/`, `goal/`, `plans/` | `/dev` deltas — problem framing, technical analysis, solution specs, `/goal` contracts, execution plans | **Issue-linked, archivable** (below) |
| `archive/YYYY-MM/` | Files moved out of the active tree by an archive wave | Cold storage — a heavy wave may be **tombstoned** (purged from `HEAD`, recoverable via git tag; see § Tombstoned waves) |
| `debt/` | Quality-debt registry (`INDEX.md` + per-pattern pages) | **Permanent** — tooled: `DEBT:` markers gated by `tools/check_debt_expiry.sh` |
| `postmortems/` | Incident write-ups | **Permanent** |
| `plans/TEMPLATE/` | Blank plan scaffold for new work | **Permanent** |
| `evidence/`, `issue-bodies/`, `dashboard-redesign/` | Rollout evidence, drafted issue bodies, visual before/after captures | **Permanent** (small, referenced by ADRs/issues) |
| `quality-debt-report.json` | Generated debt census consumed by `debt/` pages | **Permanent** (regenerated, not hand-edited) |

## Issue-linked lifecycle

`frame` / `analysis` / `spec` / `goal` / `plan` files are **deltas tied to one
GitHub issue** (ADR-086 § retrieval ladder). Basenames encode the issue number:
`<issue>-<slug>-<kind>.{md,mdx}` (most also carry an `issue:` frontmatter field).

A delta becomes **archivable** once **both** hold:

1. its issue is **closed**, and
2. the implementing PR is **merged** to `staging`.

At that point its durable value (invariants, boundary rules, decisions) must
already have graduated into a Tier-2 domain page or an ADR — the delta itself is
retired to `archive/YYYY-MM/`. Nothing in the active tree is deleted; archiving
is a move.

**No delta may be an ADR's "current truth."** ADR-086 makes `artifacts/`
deltas-only, and the doc-drift gates (`tools/check_doc_drift.py`,
`tools/check_doc_semantic_drift.py`) deliberately **exempt `artifacts/**`** — so
an ADR that delegates its live state to a file here is both a contradiction and
an ungated blind spot. If an ADR needs a "current truth" pointer, it points at a
`docs/architecture/` domain page. Migrate the invariants there first, then
archive the delta.

## Permanent categories (never swept)

`debt/`, `postmortems/`, `plans/TEMPLATE/`, `evidence/`, `issue-bodies/`,
`dashboard-redesign/`, this `README.md`, and `quality-debt-report.json` are
exempt from the archive wave — they are either tooled registries, reference
material, or scaffolds with no issue-close TTL.

## Archive wave — `tools/archive_artifacts_wave.py`

A manual/monthly sweep that retires closed-issue deltas to `archive/YYYY-MM/`.

```bash
# Dry-run (default): census closed issues, list moves, verify link-rewrite is clean.
uv run python tools/archive_artifacts_wave.py --dry-run

# Apply: rewrite every inbound reference, THEN git-mv the files.
uv run python tools/archive_artifacts_wave.py --apply
```

- **Census** — resolves each candidate's issue number (authoritative `issue:`
  frontmatter, else a non-date basename prefix) and asks GitHub (GraphQL via
  `gh`) which are closed. Only closed-issue deltas are candidates. Injectable
  (`--closed-issues-file`) so the link-rewrite logic is testable offline.
- **Mandatory link-rewrite (before the move).** For every file about to move,
  the script finds references to it **by basename** across the whole repo —
  `docs/`, `*.yml`, `skills/`, `tools/`, sibling artifacts — resolves each match
  per referring file, and rewrites it to the `archive/YYYY-MM/` path. Every
  reference form is covered: repo-root `artifacts/<cat>/<name>`, relative
  sibling `../<cat>/<name>` (the `artifacts/` prefix dropped), and same-dir bare
  names. Only after the tree is link-clean does it `git mv`.
- **Zero broken links is the acceptance bar.** The 2026-06 wave moved files
  with a naive in-repo grep and stranded 18 dead links — mostly relative sibling
  links a full-path-tail match never saw. The basename-based rewrite step and
  the dry-run link check (which resolves the same forms) exist so that never
  repeats. `--dry-run` fails loudly if any reference would be left dangling.

Graduating a "current truth" delta (migrate invariants → domain page, re-point
the ADR banner, then archive) is the same move done by hand, because the banner
re-point is a semantic edit the script cannot infer.

## Tombstoned waves

`archive/YYYY-MM/` is cold storage, but a wave heavy enough to dominate the repo
(grep/`ccc` noise, clone weight) may be **tombstoned**: removed from `HEAD` while
its full tree is preserved forever in git history under an annotated tag
`artifacts-archive/YYYY-MM`. Tombstoning is the last step, only after **every
live reference out of the archive has been migrated** (a tombstone must strand
zero inbound links — the same acceptance bar as the wave itself).

Recover any tombstoned file without un-tombstoning the wave:

```bash
git show artifacts-archive/YYYY-MM -- artifacts/archive/YYYY-MM/<path>   # one file
git restore --source artifacts-archive/YYYY-MM artifacts/archive/YYYY-MM # whole wave
```

| Wave | Tag | Files | Purged | Last live inbound ref migrated |
|------|-----|-------|--------|--------------------------------|
| `2026-06` | `artifacts-archive/2026-06` | 360 (~4.8 MB, ~44 % of repo markdown) | #2219 | `docs/runbooks/gh-key-rotation.md` → single-App decision inlined; audit Decision Log recoverable via the tag |
