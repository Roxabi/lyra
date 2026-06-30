# Runbook — Quadlet install & sync

## Architecture (nine containers)

All units attach to `roxabi.network` (systemd `--user`, linger enabled).

| Service | Image (typical) | Role |
|---|---|---|
| `factory-nats` | `docker.io/library/nats` (digest-pinned) | Message bus (port 4222) |
| `factory-hub` | `ghcr.io/roxabi/factory:staging-svc` | Hub — routing, pool, memory |
| `factory-telegram` | `ghcr.io/roxabi/factory:staging-svc` | Telegram adapter |
| `factory-discord` | `ghcr.io/roxabi/factory:staging-svc` | Discord adapter |
| `factory-clipool` | `ghcr.io/roxabi/factory:staging` | CliPool NATS worker |
| `factory-gh-helper` | `ghcr.io/roxabi/factory:staging` | GitHub App token-mint (`factory-gh.pod`) |
| `factory-turn-writer` | `ghcr.io/roxabi/factory:staging-svc` | JetStream writer for `turns.db` |
| `factory-blobstore` | `ghcr.io/roxabi/factory:staging-svc` | HTTP BlobStore (port 8449) |
| `factory-ingress` | `ghcr.io/roxabi/factory:staging-svc` | External webhooks → `factory.event.*` (ADR-096; port 8780 tailnet) |
| `factory-omp` | `ghcr.io/roxabi/factory:staging` | OmpWorker NATS backend |

Image tag split (`staging` vs `staging-svc`) → [ops/container-publishing.md](../ops/container-publishing.md).

## First-time setup

```bash
cd ~/projects/roxabi-factory
make nats-setup
./deploy/install.sh
make quadlet-install    # seeds BotStore + renders adapter templates (#1416)

systemctl --user start factory-nats
sleep 3
systemctl --user start factory-hub factory-telegram factory-discord factory-clipool \
  factory-gh-helper factory-turn-writer factory-blobstore factory-omp

uv run python deploy/nats/bootstrap_streams.py
systemctl --user status 'factory-*'
podman ps
```

## Re-deploy after code change

Auto-update handles GHCR image pulls. For manual unit refresh:

```bash
make quadlet-install
systemctl --user restart factory-hub factory-telegram factory-discord factory-clipool
# or full stack:
make converge
```

## Auto-sync (quadlet file changes)

`factory-quadlet-sync.timer` (every 5 min): `git fetch` → `ff-only pull` → conditional `make quadlet-install`.

```bash
make quadlet-sync-install   # first-time enable (idempotent)
journalctl --user -u factory-quadlet-sync -n 20
systemctl --user list-timers factory-quadlet-sync.timer
```

Use manual `make quadlet-install` when you cannot wait for the timer (hot-fix, debugging).

## JetStream monitoring streams

| Stream | Subjects | Retention | MaxAge | MaxBytes |
|---|---|---|---|---|
| `factory-events` | `factory.event.>` | Limits | 24 h | 512 MiB |
| `factory-metrics` | `factory.metric.>` | Limits | 7 d | 256 MiB |

Backed by `factory-jetstream.volume`. Provision: `uv run python deploy/nats/bootstrap_streams.py` (idempotent).