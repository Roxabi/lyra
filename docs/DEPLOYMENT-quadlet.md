# Deployment — Podman Quadlet (Machine 1)

See also: [DEPLOYMENT.md](DEPLOYMENT.md) for the deployment overview and day-to-day operations.

Production deployment for Machine 1 (`roxabituwer`, Ubuntu 26.04 LTS) using rootless Podman Quadlet units managed by systemd `--user`. This is the current production path as of #611.

> **Legacy note:** The pre-#611 supervisord stack has been removed from the repo (#886).
> It is no longer the default or recommended path.

## Which path should I pick?

| Tier | Topology | Audience |
|---|---|---|
| Dev | `lyra start` — 1 process, embedded NATS | local hacking |
| Prod (Quadlet) | 5 containers on `roxabi.network` | **default — this doc** |

**Use Quadlet** (this doc) for production — OCI isolation, reproducible images, rootless
containers, systemd-native lifecycle management.

**Dev mode** (`lyra start`) is for local hacking only — no NATS server required, single process.

## 1. Overview

Five containers run on a shared `roxabi.network` bridge, all rootless under the `lyra` user:

```
systemd --user (linger enabled)
├── lyra-nats.service          ← NATS 2.10.29-alpine (port 4222 on roxabi.network)
├── lyra-hub.service           ← Exec: lyra hub
│     PublishPort 127.0.0.1:8443:8443
├── lyra-telegram.service      ← Exec: lyra adapter telegram
├── lyra-discord.service       ← Exec: lyra adapter discord
└── lyra-clipool.service       ← Exec: lyra clipool

Volumes
├── lyra-data           → /home/lyra/.lyra            (hub rw, adapters ro)
├── lyra-logs           → /home/lyra/.local/state/lyra/logs  (all rw)
├── lyra-config         → config.toml bind mount       (ro)
└── Podman secrets
                        → /run/secrets/*.seed          (each container ro)
                        → /etc/nats/nkeys/auth.conf    (nats ro)
```

Unit files live in `deploy/quadlet/`. Quadlet generates the `.service` units from `.container`, `.volume`, and `.network` descriptors on `daemon-reload`. Service names match `ContainerName=`: `lyra-nats.service`, `lyra-hub.service`, `lyra-telegram.service`, `lyra-discord.service`, `lyra-clipool.service`.

## 2. Prerequisites

- Podman 5.x from apt (ships with Ubuntu 26.04 LTS — no PPA needed).
- Linger enabled so user units survive logout:
  ```bash
  loginctl enable-linger $USER
  ```
- Image built on Machine 2 and transferred (§3).
- nkeys generated — see [DEPLOYMENT.md §10](DEPLOYMENT.md#10-nats-acl-rollout) for `deploy/nats/gen-nkeys.sh`. The Quadlet volumes `lyra-nkey-*.volume` mount the seed files produced by that script.
- Scoped env files present on Machine 1 (§7).

## 3. Build + push image

Run on Machine 2. `DEPLOY_HOST` and `DEPLOY_DIR` must be set in `.env`.

```bash
# Build localhost/lyra:latest
make build

# Stream image to Machine 1 via SSH + podman load
make push
```

`make build` runs `podman build -f Dockerfile -t localhost/lyra:latest .`.
`make push` pipes `podman save | ssh $DEPLOY_HOST "podman load"`.

The image uses a two-stage build: builder installs deps with `uv` (no voice extras), runtime stage runs as the `lyra` user with a minimal Python 3.12 slim base.

## 4. Install units

Run on Machine 1 from `~/projects/lyra`:

```bash
make quadlet-install
```

This copies all `.network`, `.volume`, and `.container` files from `deploy/quadlet/` to `~/.config/containers/systemd/` and runs `systemctl --user daemon-reload`, which triggers Quadlet to generate the corresponding `.service` units.

Verify units were generated:

```bash
systemctl --user list-units 'lyra-*' nats.service
```

## 5. Switching from supervisord (optional)

If you previously deployed via the simple path and now want to move this host to Quadlet, follow these steps. This is uncommon — most hosts pick one path and stay on it.

The Makefile dispatcher switches between supervisord and systemd based on whether `LYRA_SUPERVISORCTL_PATH` is set in `.env`.

**Step 1 — stop and disable the supervisord stack:**

```bash
# On Machine 1
cd ~/projects/lyra
make lyra stop                              # stops lyra-hub, lyra-telegram, lyra-discord via supervisorctl
systemctl --user disable --now lyra.service # prevents supervisord from restarting on boot
```

**Step 2 — switch the dispatcher to systemd:**

In `~/projects/lyra/.env`, comment out or remove:

```bash
# LYRA_SUPERVISORCTL_PATH=/path/to/supervisorctl  ← remove or comment this line
```

With `LYRA_SUPERVISORCTL_PATH` unset, the Makefile uses `systemctl --user` for all `make lyra|telegram|discord` commands.

**Step 3 — start Quadlet services:**

```bash
systemctl --user start nats.service
systemctl --user start lyra-hub.service
systemctl --user start lyra-telegram.service
systemctl --user start lyra-discord.service
```

Or via the Makefile dispatcher (all three lyra services):

```bash
make lyra start
```

## 6. Start / stop / reload

The Makefile dispatcher (`lyra_sctl`) works identically for both backends once `LYRA_SUPERVISORCTL_PATH` is set or unset accordingly.

```bash
make lyra start    # start hub + telegram + discord
make lyra stop     # stop all three
make lyra reload   # restart all three (maps to systemctl restart)
make lyra status   # status all three
make lyra logs     # journalctl -f for lyra-hub
make lyra errors   # journalctl -f -p err for lyra-hub

make telegram reload   # restart telegram adapter only
make discord reload    # restart discord adapter only
```

Raw systemd equivalents:

```bash
systemctl --user start   lyra-hub.service lyra-telegram.service lyra-discord.service
systemctl --user stop    lyra-hub.service lyra-telegram.service lyra-discord.service
systemctl --user restart lyra-hub.service lyra-telegram.service lyra-discord.service
systemctl --user status  lyra-hub.service
```

Note: adapters declare `After=lyra-hub.service` but no `Requires=` — a hub restart does not cascade a stop to the adapters. Adapters reconnect to NATS automatically once the hub is back.

## 7. Env + secrets

**Credential source:** Bot tokens (Telegram, Discord) are Fernet-encrypted in `~/.lyra/config.db` (table `bot_secrets`) and decrypted at runtime by `LyraKeyring` using `~/.lyra/keyring.key`. The standalone adapter bootstrap reads from `config.db` directly — no env vars needed for tokens.

```bash
# Store tokens (run once per bot)
lyra bot add --platform telegram --bot-id lyra
lyra bot add --platform discord --bot-id lyra

# Verify
lyra bot list
```

**Environment inline in `.container` files:** NATS connection vars are set directly in each Quadlet unit:

```ini
Environment=NATS_URL=nats://lyra-nats:4222
Environment=NATS_NKEY_SEED_PATH=/run/secrets/<role>.seed
```

**Nkey secrets:** Seed files live in `~/.lyra/nkeys/` and are mounted as Podman secrets:

```bash
# Generate nkeys + auth.conf
make nats-setup

# Create Podman secrets
make quadlet-secrets-install
```

This creates secrets: `lyra-nats-auth`, `lyra-nkey-hub`, `lyra-nkey-telegram-adapter`, `lyra-nkey-discord-adapter`, `lyra-nkey-clipool-worker`.

`config.toml` is bind-mounted read-only from `~/projects/lyra/config.toml`.

## 8. Logs

Logs go to journald. Container stdout/stderr is captured by systemd:

```bash
journalctl --user -u lyra-hub -f
journalctl --user -u lyra-telegram -f
journalctl --user -u lyra-discord -f
journalctl --user -u lyra-nats -f

# Errors only
journalctl --user -u lyra-hub -f -p err

# Since last boot
journalctl --user -u lyra-hub -b
```

For VRAM and firewall monitoring, see [DEPLOYMENT.md §6](DEPLOYMENT.md#6-monitor-vram-machine-1) and [§7](DEPLOYMENT.md#7-firewall-ufw).

## 9. Updating code

### Automatic (recommended)

Since #929, prod uses `podman auto-update` to automatically pull new images from GHCR. The timer fires every 5 minutes:

```bash
systemctl --user is-active podman-auto-update.timer  # verify timer is active
podman auto-update --dry-run                          # check pending updates
```

Containers with `Label=io.containers.autoupdate=registry` pull new digests from `ghcr.io/roxabi/lyra:staging` and restart automatically. No manual intervention after a staging merge.

See [ops/container-publishing.md](ops/container-publishing.md#auto-update-flow) for full details.

### Manual fallback

The canonical update flow on Machine 1 is `scripts/deploy-quadlet.sh`, which pulls staging, rebuilds the image, and restarts containers:

```bash
# From Machine 2 — rebuild, push, and restart on Machine 1
make deploy-quadlet
```

`scripts/deploy-quadlet.sh` is a thin wrapper: it sets Lyra-specific variables and delegates all logic to the shared deploy library at `~/.local/lib/roxabi/deploy-lib.sh`. Install the library once:

```bash
make quadlet-install-deploy-lib
```

The library is pinned at install time (commit SHA stamped in the header). To upgrade after a Lyra update:

```bash
make quadlet-upgrade-lib
```

Manual fallback (run on Machine 1):

```bash
# On Machine 2: rebuild and push
make build && make push

# On Machine 1: restart containers to pick up new image
systemctl --user restart lyra-hub.service lyra-telegram.service lyra-discord.service
```

Or combine into one remote command:

```bash
ssh $DEPLOY_HOST "cd ~/projects/lyra && systemctl --user restart lyra-hub lyra-telegram lyra-discord"
```

## 10. Troubleshooting

**Unit not generated after quadlet-install**

`daemon-reload` triggers Quadlet's generator. If units still don't appear:

```bash
systemctl --user daemon-reload
systemctl --user list-units 'lyra-*'
# Check generator errors
journalctl --user -u systemd-user-generators -b
```

**Image not found**

```bash
podman images | grep lyra
# If missing, pull from GHCR:
podman pull ghcr.io/roxabi/lyra:staging
```

**Container exits immediately**

```bash
podman logs lyra-hub        # last run stdout/stderr
journalctl --user -u lyra-hub -n 50
# Common causes: missing config.toml, missing nkey seed, DB not seeded
```

**NATS connection refused**

```bash
systemctl --user status lyra-nats.service
journalctl --user -u lyra-nats -n 20
# Ensure NATS is running on roxabi.network
```

---

## Known gaps

- **Image digest pinning for `localhost/lyra:dev` is not yet in place.** Production uses `ghcr.io/roxabi/lyra:staging` with auto-update.
