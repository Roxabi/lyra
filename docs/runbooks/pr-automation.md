# PR merge automation

## Overview

`roxabi-factory` auto-merges PRs via two workflows: `.github/workflows/auto-merge.yml` (the
merge/close machinery) and `.github/workflows/renovate-automerge.yml` (auto-labels Renovate
dependency PRs so they enter that machinery). Both mint a short-lived `roxabi-ci` GitHub App
token rather than using `GITHUB_TOKEN` or a shared PAT.

Dependency bumps are handled by **Renovate** (`.github/renovate.json`) — uv/pep621, bun,
dockerfile, and github-actions, with `uv.lock` regen in the same PR and a global
`minimumReleaseAge` of 3 days. Dependabot version updates were removed (#2188).

The whole thing keys off one label: **`reviewed`** (`#0075ca`, "Code reviewed and approved") —
not `viewed` (a different, similarly-named label used elsewhere for acknowledgement).

Merging goes through GitHub's **merge queue** on `staging`, enabled via the "staging merge
queue" repository ruleset (a ruleset, not branch protection). Flow: `reviewed` label → arm
merge-when-ready → the PR **enqueues** once its PR-level required checks pass → the queue
builds a temporary `gh-readonly-queue/staging/*` ref (the PR merged onto the current queue
head), re-runs the required checks there via the `merge_group` event (`ci.yml` and
`secret-scan.yml` both trigger on it), and lands the merge commit on green.

## `auto-merge.yml`

| Job | Trigger | What it does |
|---|---|---|
| `auto-merge` | PR `labeled` / `synchronize` / `closed` | If the PR carries `reviewed`, runs `gh pr merge --auto --merge` (merge commit — squash is forbidden project-wide, see `~/projects/ssot/conventions.ssot.md`). Arms GitHub's merge-when-ready: the PR enqueues as soon as its PR-level required checks pass; the queue does the rest. |
| `close-linked-issues` | PR `closed` + merged | Closes `Closes #N` references in the PR body — `GITHUB_TOKEN`-initiated auto-merges don't trigger GitHub's native issue-closing behavior, so this job does it explicitly. |

The former update-behind-prs job (rebase every open non-draft PR on every `staging`/`main`
push) is **retired**: the queue validates each entry as the *actual merge result* against the
queue head, so freshness is the queue's job now — no update-branch churn (that fan-out was
67.7% of ALL CI runs pre-#2136).

Branch protection on `staging`: `required_status_checks` = `ci` + `trufflehog` +
`docker-build` with `strict: false` — the queue owns freshness, so up-to-date branches are
**no longer** required at merge time — `enforce_admins: false`, and **no required PR review
count**: merge only needs the `reviewed` label + green required checks. `main` requires the
`/promote` flow instead (see `~/projects/ssot/conventions.ssot.md`).

### Queue stalls: a flaky red dequeues the PR

If a queue entry goes red (flaky test, transient infra), GitHub **dequeues the PR and disarms
merge-when-ready**. The PR then sits indefinitely with green PR-level checks and the
`reviewed` label still applied — nothing retries it automatically, and no workflow goes red on
the PR itself (the failure lives on the temporary queue ref's run).

**Recovery:** re-arm with `gh pr merge <N> --auto --merge`, or remove + re-add the `reviewed`
label (the `labeled` event re-fires the `auto-merge` job, which re-arms). Either path
re-enqueues the PR.

**Rollback (queue off):** delete the "staging merge queue" ruleset
(`gh api repos/Roxabi/roxabi-factory/rulesets/<id> --method DELETE`) and restore
`strict: true` on the `staging` branch protection — the `merge_group:` triggers in
`ci.yml`/`secret-scan.yml` are inert without a queue, so that restores pre-queue behavior
exactly.

## `renovate-automerge.yml`

Renovate PRs only get the `dependencies` (and, for GitHub Actions bumps, `ci`) label from
`.github/renovate.json` — never `reviewed` — so left alone they never enter the merge queue.

The fix is a `pull_request_target` workflow (`opened`/`reopened`, guarded to
`github.actor == 'renovate[bot]'`) that adds the `reviewed` label when the PR does **not**
carry the `major` label (Renovate applies `major` via `packageRules` for semver-major bumps).
That label add fires the `labeled` event on `auto-merge.yml`, which arms merge-when-ready —
the PR enqueues only if CI is actually green. **Major bumps are never auto-labelled** — they
wait for human review.

**Satellites** (`voiceCLI`, `imageCLI`, `llmCLI`) use the same pattern with one extra guard:
PRs labelled `roxabi-sdk` (grouped `git-refs` bumps for `roxabi-contracts` / `roxabi-nats` /
`roxabi-blobs`) are **not** auto-labelled — wire/SDK freshness stays human-gated. See
`packages/roxabi-contracts/README.md` § Satellite pin freshness.

**Load-bearing details:**

- `pull_request_target` runs in base-repo context with full Actions secrets. This is safe here
  because the workflow never checks out or executes PR-authored code — it only applies a label.
- The label must be applied by a real-actor token (the `roxabi-ci` App token), **not** the
  default `GITHUB_TOKEN`: labels applied by `GITHUB_TOKEN` do not trigger downstream
  `labeled`-event workflows, so the `auto-merge.yml` cascade would silently never fire.
- The automation only takes effect starting with the **next** Renovate PR opened after the
  workflow itself changes: `pull_request_target` always runs the workflow definition from the
  base branch, so a PR that modifies this workflow can't self-test against its own diff.
- CI (+ `docker-build` on factory) is the actual safety gate, not the label: a labelled PR with
  a breaking bump still needs green required checks to merge.

## Pre-`reviewed` checklist

Before adding `reviewed` on a human-authored PR:

1. **`make pre-pr`** — pre-push gates, `qg plan --stage ci` on `origin/staging...HEAD`, and the
   pytest-partition collect-only gate (`docs/runbooks/quality-gates.md` § Pre-PR ritual).
2. Confirm PR-level required checks (`ci`, `trufflehog`, `docker-build`) are green.
3. Apply `reviewed` only after both — the label arms merge-when-ready; the merge queue then runs
   the **full** suite on a temporary queue ref (fail-open: empty `QG_DIFF_RANGE`).

## Troubleshooting

If a human-authored PR "won't merge" despite green CI: check for the `reviewed` label — that's
the only merge gate on `staging`. If the label is on and the PR is green but neither queued nor
merging, it was probably dequeued by a red queue entry — see § Queue stalls above. If a Renovate
PR is stuck: check for the `major` label (never auto-labelled on factory) or `roxabi-sdk` on
satellites; also whether it landed *before* the current version of `renovate-automerge.yml`.
