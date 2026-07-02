# Operations Runbooks

Step-by-step procedures for running factory in production (Podman Quadlet). These complement the generic user guide in [DEPLOYMENT.md](../DEPLOYMENT.md).

| Runbook | When to use |
|---------|-------------|
| [quadlet-install.md](quadlet-install.md) | First install, re-deploy, quadlet auto-sync |
| [bot-onboarding.md](bot-onboarding.md) | Add a bot, render adapter secrets, multi-host caveat |
| [operator-log.md](operator-log.md) | Operator audit — which log for which incident |
| [loki-query.md](loki-query.md) | Central log search — LogQL recipes (Loki + Promtail) |
| [secrets-rotation.md](secrets-rotation.md) | Rotate nkeys, GH PEM, OAuth, blobstore token |
| [secrets-disaster-recovery.md](secrets-disaster-recovery.md) | Lost NATS nkeys — wipe, regen, M₂ fan-out |
| [blobstore-backup-restore.md](blobstore-backup-restore.md) | Snapshot and restore the blob index + shards |
| [persona-soul-migration.md](persona-soul-migration.md) | Migrate `persona_json` → soul blobstore (AgentSoul v1) |
| [persona-soul-rollback.md](persona-soul-rollback.md) | Revert soul migration (DB + optional blob pointer) |
| [persona-soul-operator.md](persona-soul-operator.md) | Soul edit lag, `/reset`, OMP vs clipool |
| [quadlet-diagnostic.md](quadlet-diagnostic.md) | Status checks, common failures, auto-update fix |
| [discord-db-migration.md](discord-db-migration.md) | One-time #1721 — move discord.db to named volume |
| [outbound-audio-deploy.md](outbound-audio-deploy.md) | Deploy/rollback JetStream outbound audio (#1482) |
| [cdi-gpu-validation.md](cdi-gpu-validation.md) | GPU passthrough validation (CDI / NVIDIA) |
| [nats-ops.md](nats-ops.md) | Stream/KV message counts, hub-seed stream admin, backup/restore |

**SSoT elsewhere:** component manifest `deploy/quadlet.toml` · unit files `deploy/quadlet/` · install `deploy/install.sh` · Roxabi container standards `~/projects/docs/container-deployment-standard.md`
