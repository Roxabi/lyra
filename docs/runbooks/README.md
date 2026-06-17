# Operations Runbooks

Step-by-step procedures for running Lyra in production (Podman Quadlet). These complement the generic user guide in [DEPLOYMENT.md](../DEPLOYMENT.md).

| Runbook | When to use |
|---------|-------------|
| [quadlet-install.md](quadlet-install.md) | First install, re-deploy, quadlet auto-sync |
| [bot-onboarding.md](bot-onboarding.md) | Add a bot, render adapter secrets, multi-host caveat |
| [secrets-rotation.md](secrets-rotation.md) | Rotate nkeys, GH PEM, OAuth, blobstore token |
| [blobstore-backup-restore.md](blobstore-backup-restore.md) | Snapshot and restore the blob index + shards |
| [quadlet-diagnostic.md](quadlet-diagnostic.md) | Status checks, common failures, auto-update fix |
| [discord-db-migration.md](discord-db-migration.md) | One-time #1721 — move discord.db to named volume |
| [outbound-audio-deploy.md](outbound-audio-deploy.md) | Deploy/rollback JetStream outbound audio (#1482) |
| [cdi-gpu-validation.md](cdi-gpu-validation.md) | GPU passthrough validation (CDI / NVIDIA) |

**SSoT elsewhere:** component manifest `deploy/quadlet.toml` · unit files `deploy/quadlet/` · install `deploy/install.sh` · Roxabi container standards `~/projects/docs/container-deployment-standard.md`