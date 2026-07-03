# Contracts lock-bump — retired (#1791 stop-gap)

The factory-hosted `contracts-bump.yml` reusable workflow and per-satellite
`contracts-bump-caller.yml` cron callers were **removed** when satellites migrated to Renovate
(git-refs rule for `roxabi-contracts` / `roxabi-nats` / `roxabi-blobs`).

**Current path:** each satellite's `.github/renovate.json` — see
`packages/roxabi-contracts/README.md` § Satellite pin freshness. SDK bumps land as grouped
`roxabi sdk` PRs (label `roxabi-sdk`, human review — not auto-`reviewed`).

Historical spec: `artifacts/specs/1791-contracts-lock-bump-workflow-spec.mdx`.
