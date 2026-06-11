# Contracts lock-bump — satellite caller guide

Reusable workflow: `.github/workflows/contracts-bump.yml` (hosted in `roxabi-factory`).

Each satellite repo (`llmCLI`, `voiceCLI`, `imageCLI`) calls this workflow weekly to bump
`uv.lock` for `roxabi-contracts`, `roxabi-nats`, and (where applicable) `roxabi-blobs`.

Related: issue #1791, spec `artifacts/specs/1791-contracts-lock-bump-workflow-spec.mdx`.

---

## Behavior paths

| Scenario | Outcome |
|---|---|
| `uv.lock` unchanged after upgrade | No-op — no PR created, workflow exits clean |
| Packages changed, `CONTRACT_VERSION` same | PR created, label `reviewed` applied → auto-merge cascade |
| Packages changed, `CONTRACT_VERSION` changed | PR created, label `wire-breaking` applied → held for human review |

---

## Per-satellite caller YAML

### `Roxabi/llmCLI`

`roxabi-blobs` is absent from `llmCLI`; only `roxabi-contracts` and `roxabi-nats` are bumped.

```yaml
# .github/workflows/bump-contracts.yml  (in Roxabi/llmCLI)
name: Bump contracts lock

on:
  schedule:
    - cron: "0 6 * * 1"   # Every Monday 06:00 UTC
  workflow_dispatch:

jobs:
  bump:
    uses: Roxabi/roxabi-factory/.github/workflows/contracts-bump.yml@staging
    with:
      satellite_repo: Roxabi/llmCLI
      packages: "roxabi-contracts roxabi-nats"
    secrets:
      PAT: ${{ secrets.PAT }}
```

### `Roxabi/voiceCLI`

`roxabi-blobs` is in `optional-dependencies.nats`; include it so the lock stays consistent.

```yaml
# .github/workflows/bump-contracts.yml  (in Roxabi/voiceCLI)
name: Bump contracts lock

on:
  schedule:
    - cron: "0 6 * * 1"   # Every Monday 06:00 UTC
  workflow_dispatch:

jobs:
  bump:
    uses: Roxabi/roxabi-factory/.github/workflows/contracts-bump.yml@staging
    with:
      satellite_repo: Roxabi/voiceCLI
      packages: "roxabi-contracts roxabi-nats roxabi-blobs"
    secrets:
      PAT: ${{ secrets.PAT }}
```

> Note: voiceCLI CI runs `uv sync --dev --extra all` (no `--frozen`), unlike llmCLI/imageCLI
> which use `--frozen`. A lock-bump PR into voiceCLI will still produce a valid `uv.lock`;
> CI re-resolves instead of hard-failing — this is a softer gate.

### `Roxabi/imageCLI`

`roxabi-blobs` is a core dependency for `imageCLI`.

```yaml
# .github/workflows/bump-contracts.yml  (in Roxabi/imageCLI)
name: Bump contracts lock

on:
  schedule:
    - cron: "0 6 * * 1"   # Every Monday 06:00 UTC
  workflow_dispatch:

jobs:
  bump:
    uses: Roxabi/roxabi-factory/.github/workflows/contracts-bump.yml@staging
    with:
      satellite_repo: Roxabi/imageCLI
      packages: "roxabi-contracts roxabi-nats roxabi-blobs"
    secrets:
      PAT: ${{ secrets.PAT }}
```

---

## Prerequisites before activating a caller

For each satellite repo, verify the following before merging the caller workflow PR:

- [ ] `wire-breaking` label exists in the satellite repo (check `gh label list --repo Roxabi/<sat>`)
- [ ] `reviewed` label exists in the satellite repo
- [ ] `auto-merge.yml` workflow exists in the satellite repo and triggers on `reviewed` label
- [ ] `secrets.PAT` is set in the satellite repo's Actions secrets (repo scope, covering `Roxabi/roxabi-factory`)
- [ ] `staging` branch exists and is the default branch in the satellite repo

---

## Rollout checklist — follow-up PRs (#1840)

Three follow-up PRs are needed, one per satellite (tracked in #1840):

1. `Roxabi/llmCLI` — add `.github/workflows/bump-contracts.yml` (packages: `roxabi-contracts roxabi-nats`)
2. `Roxabi/voiceCLI` — add `.github/workflows/bump-contracts.yml` (packages: `roxabi-contracts roxabi-nats roxabi-blobs`)
3. `Roxabi/imageCLI` — add `.github/workflows/bump-contracts.yml` (packages: `roxabi-contracts roxabi-nats roxabi-blobs`)

For each: verify prerequisites above → open PR against satellite `staging` → apply `reviewed` label → auto-merge.

---

## Manual trigger (factory)

To manually trigger a bump for any satellite without waiting for the weekly cron:

```bash
gh workflow run contracts-bump.yml \
  --repo Roxabi/roxabi-factory \
  --ref staging \
  -f satellite_repo=Roxabi/llmCLI \
  -f packages="roxabi-contracts roxabi-nats"
```
