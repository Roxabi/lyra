# Deployment — Podman Quadlet (Machine 1)

See also: [DEPLOYMENT.md](DEPLOYMENT.md) for the deployment overview and day-to-day operations.

Production deployment for Machine 1 (`roxabituwer`, Ubuntu 26.04 LTS) using rootless Podman Quadlet units managed by systemd `--user`. This is the current production path as of #611.

> **Legacy note:** The pre-#611 supervisord stack has been removed from the repo (#886).
> It is no longer the default or recommended path.

## Which path should I pick?

| Tier | Topology | Audience |
|---|---|---|
| Dev | `lyra start` — 1 process, embedded NATS | local hacking |
| Prod (Quadlet) | 5 containers on `lyra.network` | **default — this doc** |

**Use Quadlet** (this doc) for production — OCI isolation, reproducible images, rootless
containers, systemd-native lifecycle management.

**Dev mode** (`lyra start`) is for local hacking only — no NATS server required, single process.

## 1. Overview

Five containers run on a shared `lyra.network` bridge, all rootless under the `lyra` user:

```
systemd --user (linger enabled)
├── nats.service              ← NATS 2.10.29-alpine (pinned by digest)
│     PublishPort 127.0.0.1:4223:4222
├── lyra-hub.service          ← Exec: lyra hub
│     PublishPort 127.0.0.1:8443:8443
├── lyra-telegram.service     ← Exec: lyra adapter telegram
├── lyra-discord.service      ← Exec: lyra adapter discord
└── lyra-clipool.service      ← Exec: lyra clipool

Volumes
├── lyra-data          → /home/lyra/.lyra            (hub rw, adapters ro)
├── lyra-logs          → /home/lyra/.local/state/lyra/logs  (all rw)
├── lyra-config        → /app/config.toml             (bind ro, ~/.lyra/config.toml)
├── lyra-nats-auth     → /etc/nats/nkeys/auth.conf    (nats ro)
└── lyra-nkey-{hub,telegram-adapter,discord-adapter,...}.volume
                       → /run/secrets/*.seed          (each container ro)
```

Unit files live in `deploy/quadlet/`. Quadlet generates the `.service` units from `.container`, `.volume`, and `.network` descriptors on `daemon-reload`. Service names match `ContainerName=`: `lyra-hub.service`, `lyra-telegram.service`, `lyra-discord.service`, `nats.service`.

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

**Credential source (since #417, March 2026):** Bot tokens (Telegram, Discord) are Fernet-encrypted in `~/.lyra/config.db` (table `bot_secrets`) and decrypted at runtime by `LyraKeyring` using `~/.lyra/keyring.key`. The standalone adapter bootstrap (`src/lyra/bootstrap/standalone/adapter_standalone.py`) reads from `config.db` directly — no env var is needed or read for tokens.

**What the env file is for:** only `lyra-hub` reads an env file, and only for operational vars:

| Container | `EnvironmentFile=` | Required vars |
|---|---|---|
| `lyra-hub` | `%h/.lyra/env/hub.env` | `LYRA_HEALTH_SECRET`, `LYRA_HEALTH_PORT` |
| `lyra-telegram` | — | (tokens come from `config.db`) |
| `lyra-discord` | — | (tokens come from `config.db`) |

`%h` expands to `$HOME` in Quadlet unit files.

**Bootstrap recipe** (run once on Machine 1 before the first deploy):

```bash
mkdir -p ~/.lyra/env && chmod 700 ~/.lyra/env
cp deploy/quadlet/hub.env.example ~/.lyra/env/hub.env
chmod 600 ~/.lyra/env/hub.env
# fill in LYRA_HEALTH_SECRET with a random string
```

Bot tokens do not need to be set here. Verify they are present in `config.db` before starting:

```bash
uv run lyra secrets list   # or equivalent CLI to inspect config.db entries
```

**Keyring rotation:** after rotating `~/.lyra/keyring.key`, restart all three containers — hub, telegram, and discord — to force `LyraKeyring` to re-read the new key from disk.

**Dev tier:** `lyra start` (single process, embedded NATS) reads `~/projects/lyra/.env` — a single unsplit file documented in [DEPLOYMENT.md §2](DEPLOYMENT.md). Quadlet credential model is independent of dev tier.

`NATS_URL` and `NATS_NKEY_SEED_PATH` are set inline in each `.container` file — they do not belong in the scoped env files:

```ini
Environment=NATS_URL=nats://lyra-nats:4222
Environment=NATS_NKEY_SEED_PATH=/run/secrets/<role>.seed
```

nkey seed files are mounted as named volumes from `lyra-nkey-<role>.volume` into `/run/secrets/<role>.seed` (read-only). See `deploy/quadlet/*.volume` for the full list of nkey volumes. Seed files are generated by `deploy/nats/gen-nkeys.sh` — refer to [DEPLOYMENT.md §10](DEPLOYMENT.md#10-nats-acl-rollout).

`config.toml` is bind-mounted read-only from `~/.lyra/config.toml` via the `lyra-config.volume` (requires `~/.lyra/config.toml` to exist before starting units).

**Note:** The NATS container publishes on port 4223 by default to avoid conflicting with a host NATS instance that may already occupy 4222. If no host NATS is running, switch `PublishPort=127.0.0.1:4223:4222` to `4222:4222` in `nats.container` and update `NATS_URL` in the scoped env files.

## 8. Logs

Logs go to journald. No rotating files on disk (supervisord logs at `~/.local/state/lyra/logs/` are not written by containers — that path is still mounted but used by the app's internal log writer; container stdout goes to journald).

```bash
journalctl --user -u lyra-hub -f
journalctl --user -u lyra-telegram -f
journalctl --user -u lyra-discord -f
journalctl --user -u nats -f

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
# If missing, re-run make push from Machine 2
```

**Volume permission error**

Named volumes are created with rootless ownership. If a bind-mount volume fails (`lyra-config.volume`), check that `~/.lyra/config.toml` exists:

```bash
ls -la ~/.lyra/config.toml
```

**Container exits immediately**

```bash
podman logs lyra-hub        # last run stdout/stderr
journalctl --user -u lyra-hub -n 50
# Most common causes: missing env file, missing nkey seed, config.toml absent
```

**NATS port conflict**

The Quadlet NATS binds to `127.0.0.1:4223` by default. If another NATS instance is already on `4222`, there is no conflict. If port 4222 is free, update `nats.container` to `4222:4222` and adjust `NATS_URL` in the scoped env files.

---

## Known gaps

- **Image digest pinning for `localhost/lyra:latest` is not yet in place.** The `.container` files note this with a comment referencing a CI tag-by-SHA follow-up issue. Currently the `latest` tag floats.
- **NATS port 4223 coexistence.** `nats.container` publishes `127.0.0.1:4223:4222` to avoid conflicting with a host NATS instance on 4222. If no host NATS is present, change this to `4222:4222` in `nats.container` and update `NATS_URL` in the scoped env files accordingly.
- **`make remote` is supervisord-only.** The `remote` target SSHes and invokes `supervisorctl` unconditionally. It has no systemd branch equivalent to the local `lyra_sctl` dispatcher.
