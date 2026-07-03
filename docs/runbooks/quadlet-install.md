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

Image tag split (`staging` vs `staging-svc`) → [container-publishing.md](container-publishing.md).

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

## Deploy-time verification

`make quadlet-install` does more than copy files. After copying all
`.network`, `.volume`, and `.container` files to `~/.config/containers/systemd/`
it runs `deploy/quadlet-install-verify.sh`, which:

1. Runs `systemctl --user daemon-reload` — triggers the Quadlet generator to
   produce fresh `.service` units from the copied files.
2. Restarts (or starts) each container unit. The unit list is derived at runtime
   from `deploy/quadlet.toml` (`quadlet_containers` in `deploy/lib/quadlet-units.sh`,
   `mapfile` in `quadlet-install-verify.sh`) — it auto-updates as components are added,
   so no fixed roster is hardcoded here.
3. Waits up to 10 s per unit and checks `systemctl --user is-active`.
4. If any unit is not `active`, dumps the last 20 lines of
   `journalctl --user -u <unit>` and exits non-zero — the deploy fails loudly.

This means a broken Quadlet file (e.g. an inline `#` comment on a `Volume=`
line, which was the root cause of the 2026-05-06 incident) is caught immediately
at deploy time rather than lying dormant until the next reboot.

### Escape hatch — `NO_RESTART=1`

```bash
make quadlet-install NO_RESTART=1
```

Skips steps 1-4 (daemon-reload, restart, and verification). Only the file
copy runs. Use this when:

- Performing a manual recovery where one or more units are intentionally not
  running (e.g. after an nkey rotation before new seeds are in place).
- Deploying on a host that does not yet have the full secrets set up (initial
  bootstrap before `~/.roxabi/factory/env/` files exist).

After fixing the underlying issue, run a normal `make quadlet-install` (without
`NO_RESTART=1`) to verify all units come up.
