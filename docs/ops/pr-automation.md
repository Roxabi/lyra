# PR merge automation

## Overview

`roxabi-factory` auto-merges PRs via two workflows: `.github/workflows/auto-merge.yml` (the
merge/rebase/close machinery) and `.github/workflows/dependabot-automerge.yml` (auto-labels
Dependabot PRs so they enter that machinery). Both mint a short-lived `roxabi-ci` GitHub App
token rather than using `GITHUB_TOKEN` or a shared PAT.

The whole thing keys off one label: **`reviewed`** (`#0075ca`, "Code reviewed and approved") —
not `viewed` (a different, similarly-named label used elsewhere for acknowledgement).

## `auto-merge.yml`

| Job | Trigger | What it does |
|---|---|---|
| `auto-merge` | PR `labeled`/`synchronize`/`closed`, `check_suite` completed | If the PR carries `reviewed`, runs `gh pr merge --auto --merge` (merge commit — squash is forbidden project-wide, see `~/projects/ssot/conventions.ssot.md`). Arms GitHub's native auto-merge; it fires once required checks go green. |
| `update-behind-prs` | `push` to `staging`/`main` | Rebases every open **non-draft** PR targeting that branch via the `update-branch` API (additive merge-update, not a force-push). Runs on ALL open non-draft PRs, not just `reviewed` ones, so PRs don't rot as BEHIND while waiting for review. |
| `close-linked-issues` | PR `closed` + merged | Closes `Closes #N` references in the PR body — `GITHUB_TOKEN`-initiated auto-merges don't trigger GitHub's native issue-closing behavior, so this job does it explicitly. |

Branch protection on `staging`: `required_status_checks` = `ci` + `trufflehog` (strict/up-to-date
required), `enforce_admins: false`, and **no required PR review count** — merge only needs the
`reviewed` label + green required checks + an up-to-date branch. `main` requires the `/promote`
flow instead (see `~/projects/ssot/conventions.ssot.md`).

### `gh pr merge --auto` clean-status race

If a PR is **already fully green** at the moment `reviewed` is applied, `gh pr merge --auto
--merge` errors ("Pull request is in clean status, nothing to wait for") and the PR does **not**
merge — the job goes red, the PR shows `UNSTABLE`, `autoMerge` stays off. Only PRs that still had
a check in flight at label-time arm and merge automatically. Labelling several PRs in the same
batch: expect only one to merge outright.

**Recovery:** re-run `gh pr merge <N> --auto --merge` manually. Once the first merge lands on
`staging`, the remaining PRs become `BEHIND` (no longer "clean"), so `--auto` arms cleanly on
them and `update-behind-prs` rebases them — the cascade self-sustains until all are merged.
Plain `gh pr merge --merge` (no `--auto`) is rejected by branch protection while a PR is BEHIND
("base branch policy prohibits the merge"); always use `--auto`, not `--admin`.

## `dependabot-automerge.yml`

Dependabot PRs only get the `dependencies` (and, for GitHub Actions bumps, `ci`) label from
`.github/dependabot.yml` — never `reviewed` — so left alone they never enter the `auto-merge`
job and (pre-fix) never got rebased either, causing a pileup of unmergeable BEHIND PRs.

The fix is a `pull_request_target` workflow (`opened`/`reopened`/`synchronize`, guarded to
`github.actor == 'dependabot[bot]'`) that runs `dependabot/fetch-metadata` and, for
`version-update:semver-{patch,minor}` bumps (including grouped `minor-and-patch` PRs), adds the
`reviewed` label. That label add fires the `labeled` event on `auto-merge.yml`, which arms
GitHub's native auto-merge — merge happens only if CI is actually green. **Major bumps are never
auto-labelled** — they wait for human review.

**Load-bearing details:**

- `pull_request_target` is required, not plain `pull_request`: Dependabot-triggered runs on a
  plain `pull_request` trigger only receive Dependabot's own restricted secret set, not repo
  Actions secrets — the App token mint would fail. `pull_request_target` runs in base-repo
  context with full Actions secrets. This is safe here specifically because the workflow never
  checks out or executes PR-authored code — it only reads Dependabot metadata via the API and
  applies a label.
- The label must be applied by a real-actor token (the `roxabi-ci` App token here — historically
  a shared PAT, migrated off in the 2026-06 secrets overhaul), **not** the default
  `GITHUB_TOKEN`: labels applied by `GITHUB_TOKEN` do not trigger downstream `labeled`-event
  workflows, so the `auto-merge.yml` cascade would silently never fire.
- `fetch-metadata`'s `update-type` output correctly resolves Dependabot's *grouped* pip PRs
  (`dependabot.yml` groups `minor-and-patch` updates) — `maxSemver()` across the group still
  yields `patch`/`minor` when appropriate, so grouped PRs still get labelled.
- The automation only takes effect starting with the **next** Dependabot PR opened after the
  workflow itself changes: `pull_request_target` always runs the workflow definition from the
  base branch, so a PR that modifies this workflow can't self-test against its own diff.
- CI is the actual safety gate, not the label: a labelled PR with a breaking bump still needs
  green `ci`/`trufflehog` to merge (e.g. a `typer` 0.26 minor bump that broke two tests stayed
  open despite being labelled).

## Troubleshooting

If a human-authored PR "won't merge" despite green CI: check for the `reviewed` label — that's
the only merge gate on `staging`. If a Dependabot PR is stuck: check its update-type (majors are
never auto-labelled) and whether it landed *before* the current version of
`dependabot-automerge.yml`.
