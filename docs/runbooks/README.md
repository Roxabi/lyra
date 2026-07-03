# Operations Runbooks

Step-by-step procedures for running factory in production (Podman Quadlet). This is the
**single home** for operational procedures — the former `docs/ops/` folder was folded in
here (#2213); consumed incident postmortems live in [`docs/history/`](../history/). These
complement the generic user guide in [DEPLOYMENT.md](../DEPLOYMENT.md).

## Install & deploy

| Runbook | When to use |
|---------|-------------|
| [quadlet-install.md](quadlet-install.md) | First install, re-deploy, quadlet auto-sync |
| [quadlet-diagnostic.md](quadlet-diagnostic.md) | Status checks, common failures, auto-update fix |
| [container-publishing.md](container-publishing.md) | CI → GHCR → Quadlet publish pipeline, auto-update, rollback |
| [deploy-smoke-canary.md](deploy-smoke-canary.md) | Post-deploy smoke test + canary rollout |
| [cdi-gpu-validation.md](cdi-gpu-validation.md) | GPU passthrough validation (CDI / NVIDIA) |

## Bots & agents

| Runbook | When to use |
|---------|-------------|
| [bot-onboarding.md](bot-onboarding.md) | Add a bot, render adapter secrets, multi-host caveat |
| [bot-secrets-migration.md](bot-secrets-migration.md) | One-shot #1057 — migrate legacy `bot_secrets` rows to Podman secrets |
| [persona-soul.md](persona-soul.md) | Soul edit lag / `/reset`, `persona_json` → soul migration + rollback |
| [ingress-webhooks.md](ingress-webhooks.md) | GitHub App ingress webhooks, triage `unknown_installation` |
| [discord-db-migration.md](discord-db-migration.md) | One-time #1721 — move `discord.db` to named volume |
| [blobstore-backup-restore.md](blobstore-backup-restore.md) | Snapshot and restore the blob index + shards |

## Secrets & identity

| Runbook | When to use |
|---------|-------------|
| [secrets-rotation.md](secrets-rotation.md) | Rotate nkeys, GH PEM, OAuth, blobstore token |
| [secrets-disaster-recovery.md](secrets-disaster-recovery.md) | Lost NATS nkeys — wipe, regen, M₂ fan-out |
| [nkey-rotation.md](nkey-rotation.md) | NATS nkey compromise rotation (seed replacement) |
| [gh-key-rotation.md](gh-key-rotation.md) | Rotate the GitHub App PEM (clipool / gh-helper) |

## NATS / JetStream

| Runbook | When to use |
|---------|-------------|
| [nats-authconf-update.md](nats-authconf-update.md) | Routine ACL / `auth.conf` changes (no seed replacement) |
| [nats-identity-lifecycle.md](nats-identity-lifecycle.md) | Add / retire a NATS identity |
| [nats-ops.md](nats-ops.md) | Stream/KV counts, admin, backup/restore; outbound-audio deploy (#1482) |
| [clipool-uid-model.md](clipool-uid-model.md) | Clipool git behavior + UID trust model (`safe.directory`, userns) |

## Observability

| Runbook | When to use |
|---------|-------------|
| [operator-log.md](operator-log.md) | Operator audit — which log for which incident |
| [loki-query.md](loki-query.md) | Central log search — LogQL recipes (Loki + Promtail) |
| [otel-traces.md](otel-traces.md) | OpenTelemetry trace inspection |

## CI, release & quality

| Runbook | When to use |
|---------|-------------|
| [contracts-bump-callers.md](contracts-bump-callers.md) | Cross-repo `contracts-bump.yml` workflow (callers, secrets) |
| [pr-automation.md](pr-automation.md) | Merge queue, auto-merge label gate, Renovate auto-labelling |
| [quality-gates.md](quality-gates.md) | Quality-gates index (`stack.yml` SSoT, stages, deploy gates) |

**SSoT elsewhere:** component manifest `deploy/quadlet.toml` · unit files `deploy/quadlet/` · install `deploy/install.sh` · Roxabi container standards `~/projects/docs/container-deployment-standard.md`
