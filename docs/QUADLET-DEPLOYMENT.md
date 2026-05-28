# Quadlet Deployment — Lyra

Runbook for installing, operating, and rotating secrets in the Lyra Quadlet deployment on M₁ (`roxabituwer`).

→ SSoT standards: `~/projects/docs/container-deployment-standard.md`
→ Component manifest: `deploy/quadlet.toml`
→ Idempotent install script: `deploy/install.sh`

## Architecture

Eight containers on `roxabi.network` (systemd `--user`, linger enabled):

| Service | Container | Role |
|---|---|---|
| `lyra-nats` | NATS 2.x | Message bus (port 4222) |
| `lyra-hub` | lyra | Hub — routing, pool, memory |
| `lyra-telegram` | lyra | Telegram adapter |
| `lyra-discord` | lyra | Discord adapter |
| `lyra-clipool` | lyra | CliPool NATS worker (Claude subprocesses) |
| `lyra-gh-helper` | lyra | GitHub App token-mint helper (`lyra-gh.pod`) |
| `lyra-turn-writer` | lyra | JetStream subscriber-writer for turns.db (#1331) |
| `lyra-blobstore` | lyra | HTTP-fronted BlobStore service (port 8449, #1330) |

## Install

### First-time setup

```bash
# 1. Generate nkeys + auth.conf (if not done yet)
cd ~/projects/lyra
make nats-setup

# 2. Install secrets + static units
cd ~/projects/lyra
./deploy/install.sh

# 3. Seed BotStore + render adapter templates + daemon-reload
#    Required since #1416: hub bootstraps auth from BotStore (~/.lyra/config.db),
#    not from config.toml directly. Skipping this step causes a hub crash-loop.
make quadlet-install

# 4. Start services
systemctl --user start lyra-nats lyra-hub lyra-telegram lyra-discord lyra-clipool lyra-gh-helper

# 5. Provision JetStream monitoring streams (idempotent)
./deploy/nats/bootstrap-streams.sh

# 6. Verify
systemctl --user status 'lyra-*'
podman ps
```

### Re-deploy after code change

Auto-update handles this for GHCR images. Manual:

```bash
cd ~/projects/lyra
make quadlet-install    # copy units + daemon-reload
# then restart affected services
systemctl --user restart lyra-hub lyra-telegram lyra-discord lyra-clipool
```

## Auto-sync for Quadlet file changes

Tracked Quadlet files (`deploy/quadlet/**`, `Makefile`, `tools/render_quadlet.py`) are
auto-converged on M₁ by `lyra-quadlet-sync.timer`:

| Path | Cadence | What it does |
|---|---|---|
| **Auto** (`lyra-quadlet-sync.timer`) | Every 5 min | `git fetch` → `ff-only pull` → diff-guard → conditional `make quadlet-install` |
| **Manual** (`make quadlet-install`) | Operator-driven | Immediate — use when you need a change NOW or the timer is disabled |

### First-time enable

Run once (idempotent):
```bash
cd ~/projects/lyra
make quadlet-sync-install
```

### Checking sync status

```bash
# Last run output
journalctl --user -u lyra-quadlet-sync -n 20

# Timer next trigger
systemctl --user list-timers lyra-quadlet-sync.timer
```

### When to use manual `make quadlet-install`

- You need a Quadlet change applied **immediately** (timer max latency = 5 min).
- You are debugging a unit and want to iterate fast.
- The auto-sync service is temporarily stopped for maintenance.
- The change is a hot-fix and you cannot wait for the next timer firing.

## Secret layout

| Podman secret | Source file | Mounted at |
|---|---|---|
| `lyra-nats-auth` | `~/.lyra/nkeys/auth.conf` | `/etc/nats/nkeys/auth.conf` (in lyra-nats) |
| `lyra-nats-hub` | `~/.lyra/nkeys/hub.seed` | `/run/secrets/lyra-nats-hub.seed` |
| `lyra-nats-telegram` | `~/.lyra/nkeys/telegram-adapter.seed` | `/run/secrets/lyra-nats-telegram.seed` |
| `lyra-nats-discord` | `~/.lyra/nkeys/discord-adapter.seed` | `/run/secrets/lyra-nats-discord.seed` |
| `lyra-nats-clipool` | `~/.lyra/nkeys/clipool-worker.seed` | `/run/secrets/lyra-nats-clipool.seed` |
| `lyra-gh-pem` | `~/.lyra/gh-app.pem` | `/run/secrets/gh-app.pem` (helper only) |
| `lyra-claude-oauth` | `~/.lyra/claude-oauth.tok` | `CLAUDE_CODE_OAUTH_TOKEN` env (clipool) |
| `lyra_blobstore_token` | `~/.lyra/blobstore.tok` | `/run/secrets/lyra_blobstore_token` (uid=1500, gid=1500, mode=0400) |

All seed secrets use `type=mount` (tmpfs-backed). `lyra-claude-oauth` uses `type=env`. (S7, S18)
`lyra_blobstore_token` uses `type=mount`; the bearer token is read once at container startup by
the auth middleware and never re-read until the container restarts (ADR-054).

## JetStream streams

| Stream | Subjects | Retention | MaxAge | MaxBytes |
|---|---|---|---|---|
| `lyra-events` | `lyra.event.>` | Limits | 24 h (hot) | 512 MiB |
| `lyra-metrics` | `lyra.metric.>` | Limits | 7 d (warm) | 256 MiB |

Both streams use `StorageType.FILE` backed by `lyra-jetstream.volume` (`~/.lyra/nats/jetstream`).
Provisioning is idempotent via `./deploy/nats/bootstrap-streams.sh` (called in first-time setup above).

Ops decision (#1183): events = 24 h hot (high churn, dashboard real-time), metrics = 7 d warm
(trending / SLA review). Both are `Limits` retention so multiple consumers can read the same
message; durable consumers for the future dashboard are tracked in #1035.

## Bot credentials

### (a) No manual splicing

The tracked files `deploy/quadlet/lyra-{telegram,discord}.container.tmpl` are **pure templates** — they contain a `{{bot_secrets}}` marker and zero `Secret=lyra-bot-*` lines. Per-bot `Secret=lyra-bot-<platform>-<bot_id>,…` directives are generated at install time by `tools/render_quadlet.py`, which reads bot rows from `BotStore` (`~/.lyra/config.db`) and substitutes the rendered block into each template before writing the live Quadlet to `~/.config/containers/systemd/`.

**Never edit `~/.config/containers/systemd/lyra-{telegram,discord}.container` directly.** Any manual change is silently overwritten on the next `make quadlet-install`. This replaces the prior fragment-paste workflow that caused the 2026-05-26 crash-loop cascade (#1369): a `git pull --ff-only` during the #1331 deploy discarded an operator-spliced `BEGIN/END` block, stranding 4 bot-token mounts and forcing 6.5 h of adapter crash-loop before re-splice. Use the onboarding flow in (b) instead.

### (b) Bot onboarding

End-to-end flow for adding a new bot:

1. Add the bot to the DB (DB-first since #1415):
   ```bash
   lyra agent <platform> add <bot_id> --agent <agent>
   ```
   (Replace `<platform>` with `telegram` or `discord`.)

2. Install the Podman secret for the bot token (host-local):
   ```bash
   lyra bot secret install <platform> <bot_id>
   ```
   This creates `lyra-bot-<platform>-<bot_id>` in the Podman secret store. For webhook variants, also run:
   ```bash
   lyra bot secret install <platform> <bot_id>-webhook
   ```

3. Render and restart:
   ```bash
   make quadlet-install
   ```
   The target runs `lyra bot init` (idempotent re-sync of `config.toml` → `BotStore`), then `render_quadlet.py` reads from `BotStore`, generates the `Secret=` directives, writes the new Quadlet atomically, runs `systemctl --user daemon-reload`, then restarts the adapter container.

4. Verify the adapter is healthy:
   ```bash
   systemctl --user status lyra-<platform>
   ```
   Expected: `Active: active (running)` with `NRestarts=0`.

> **Why `systemctl --user restart` is mandatory after a `Secret=` change:** `systemctl daemon-reload` regenerates the transient `.service` from the new Quadlet definition but does NOT propagate `Secret=` mount changes into an already-running container. The new tmpfs mount only takes effect when the container is (re)started. `make quadlet-install` issues the restart automatically — do not skip it.

### (c) CI guard

Three artefacts prevent re-introduction of the manual-splice pattern:

- `tools/check_quadlet_template_purity.sh` — enforces three invariants on all tracked `deploy/quadlet/*.container.tmpl` files: (i) no `Secret=lyra-bot-*` lines present; (ii) no `# --- BEGIN … ---` splice-block markers present; (iii) `tools/render_quadlet.py` exists (guards against accidental deletion of the render script).
- `make quadlet-lint` — local entry point; run before pushing to catch purity failures early.
- `.github/workflows/quadlet-lint.yml` — CI job that runs the purity check on every PR touching `deploy/quadlet/**`. A PR that reintroduces a `Secret=lyra-bot-` line or a `BEGIN/END` splice block into any `.container.tmpl` will fail here.

### (d) Multi-host caveat

`~/.lyra/config.toml` is Syncthing-synced across M₁, M₂, and laptop. That means `[[auth.telegram_bots]]` and `[[auth.discord_bots]]` enumerate **all bots across all hosts** in one shared file. `make quadlet-install` runs `lyra bot init` first (seeds `BotStore` from `config.toml`), then `render_quadlet.py` reads from `BotStore` — so the rendered Quadlet on every host includes `Secret=` lines for every configured bot.

However, Podman secrets (`lyra-bot-<platform>-<bot_id>`) are **host-local** and must be installed per host. A mismatch — `config.toml` lists a bot but its Podman secret is absent — causes `systemctl --user start lyra-<platform>` to fail immediately:

```
Error: looking up secret name "lyra-bot-telegram-<bot_id>": no such secret
```

**Mitigation:** When adding a bot, run `lyra bot secret install <platform> <bot_id>` on **every host that runs the adapter for that platform** before executing `make quadlet-install`.

Current host-role mapping (from `~/projects/hosts.toml`):

| Host | Role | Adapter units |
|---|---|---|
| M₁ `roxabituwer` | `lyra-hub` | `lyra-telegram`, `lyra-discord` (all 4 bots) |
| M₂ `roxabitower` | `image-worker` | none today |

## Secret rotation

### Rotate an nkey seed

```bash
# 1. Generate new seed
cd ~/projects/lyra
make nats-regen-authconf    # re-renders auth.conf; auto-creates missing seeds

# 2. Re-install the affected secret
podman secret create --replace lyra-nats-hub ~/.lyra/nkeys/hub.seed

# 3. Reload NATS (auth.conf change), then restart the affected container
systemctl --user restart lyra-nats
systemctl --user restart lyra-hub
```

### Rotate all nkey secrets at once

```bash
cd ~/projects/lyra
./deploy/install.sh --force --secrets-only
systemctl --user restart lyra-nats lyra-hub lyra-telegram lyra-discord lyra-clipool
```

### Rotate GitHub App PEM

```bash
# Copy new PEM to ~/.lyra/gh-app.pem, then:
podman secret create --replace lyra-gh-pem ~/.lyra/gh-app.pem
systemctl --user restart lyra-gh-helper
```

### Rotating the BlobStore bearer token

`lyra_blobstore_token` is a `type=mount` secret — the tmpfs file is bound at container init.
`podman secret create --replace` alone does NOT refresh the in-container file; a restart is
mandatory (ADR-054). Keep the prior source file as `.prev` until rotation is confirmed.

1. Write the new token to the host source file:
   ```bash
   printf '%s' "$NEW_TOK" > ~/.lyra/blobstore.tok && chmod 0600 ~/.lyra/blobstore.tok
   ```
   Keeping the source file at `~/.lyra/blobstore.tok` ensures reinstall scripts remain idempotent.

2. Update the Podman secret store:
   ```bash
   podman secret create --replace lyra_blobstore_token ~/.lyra/blobstore.tok
   ```

3. Restart the service so the new tmpfs file is bound at container init:
   ```bash
   systemctl --user restart lyra-blobstore.service
   ```

4. Verify the new token is in place (first 8 chars should match `$NEW_TOK`):
   ```bash
   podman exec lyra-blobstore sh -c 'head -c 8 /run/secrets/lyra_blobstore_token'
   ```

5. Rollback (if step 4 fails — keep `.prev` until this step is confirmed unnecessary):
   ```bash
   podman secret create --replace lyra_blobstore_token ~/.lyra/blobstore.tok.prev
   systemctl --user restart lyra-blobstore.service
   ```

## Backing up the BlobStore

> **Host vs container path:** `/data/lyra/blobs` is the canonical host path. `~/.lyra/blobstore` is the container view (bind-mounted into `lyra-blobstore` via `Volume=/data/lyra/blobs:/home/lyra/.lyra/blobstore:z`). All backup and restore commands below reference the canonical host path.

The BlobStore consists of two parts that must be snapshotted in order: the SQLite index
(`/data/lyra/blobs/index.sqlite`) first, then the content-addressed shard tree
(`/data/lyra/blobs/`). Reversing the order risks capturing a `blob_refs` row
whose shard file was not yet in the snapshot — a phantom row at restore time.

`FsBlobStore` uses a per-instance `asyncio.Lock`, not a global write-quiesce. A concurrent
`put()` between step 1 and step 2 can leave a shard file in the snapshot whose `blob_refs`
row was NOT captured in the DB snapshot. The restore invariant handles this safely — see
`## Restore invariant` below.

1. Snapshot the SQLite index (atomic per the SQLite `.backup` API):
   ```bash
   mkdir -p /tmp/blobstore-snapshot
   sqlite3 /data/lyra/blobs/index.sqlite ".backup '/tmp/blobstore-snapshot/index.sqlite'"
   ```

2. Snapshot the shard tree together with the DB snapshot (use hardlinks to minimise disk usage):
   ```bash
   cp -al /data/lyra/blobs /tmp/blobstore-snapshot/blobs
   ```
   For off-host backup, pipe through Restic or similar:
   ```bash
   tar -cf - /tmp/blobstore-snapshot | restic backup --stdin --stdin-filename blobstore.tar
   ```

3. Verify the snapshot (should report 0 mismatches):
   ```bash
   sha256sum -c <(find /tmp/blobstore-snapshot/blobs -type f -exec sha256sum {} +)
   ```

Recommended cadence: daily, scheduled when traffic is low. Store alongside `~/.lyra/config.db`
backups.

## Restore invariant

After extracting a backup, the SQLite manifest is the authoritative record. Any shard file
under the prefix-shard tree (`<root>/<sha[:2]>/<sha>`) that is NOT referenced by a `blobs.store_path`
row is a content-addressed orphan produced by a `put()` that wrote the file but did not complete
its `INSERT blob_refs` before the DB snapshot was taken. These orphans are always safe to
discard — no consumer ever held a `store_key` pointing to them.

Reconciliation procedure after restore:

1. List expected files (from the DB):
   ```bash
   sqlite3 index.sqlite "SELECT store_path FROM blobs"
   ```

2. List actual files on disk:
   ```bash
   find . -type f
   ```

3. Discard files in (2) that are absent from (1) — they are dedup-orphans, never user data loss.

The reverse case — a `blob_refs` row pointing to a missing shard file — is the unrecoverable
bug that the step-1-before-step-2 ordering prevents.

→ See `docs/architecture/storage.md` (BlobStore section) for the write-durability invariant
that underpins this restore procedure.

## Host-level mount requirements

The `/data/lyra/blobs` filesystem must be mounted with `noatime` and `nodiratime` on the host. This prevents every blob read (GET, HEAD, or consistency check) from updating the inode `atime`, which would otherwise generate unnecessary write I/O and accelerate SSD wear on the content-addressed shard tree.

Verify current mount options:

```bash
findmnt -n -o OPTIONS /data/lyra/blobs
```

If `noatime` is missing, update `/etc/fstab` and remount:

```bash
# Example fstab entry
/dev/mapper/data-lyra-blobs  /data/lyra/blobs  ext4  defaults,noatime,nodiratime  0  2
```

Apply without reboot:

```bash
sudo mount -o remount,noatime,nodiratime /data/lyra/blobs
```

## Diagnostic

```bash
# Service status
systemctl --user status 'lyra-*'

# Running containers
podman ps --filter 'name=lyra'

# Logs
journalctl --user -u lyra-hub -f
journalctl --user -u lyra-telegram -f
journalctl --user -u lyra-clipool -f
journalctl --user -u lyra-gh-helper -f

# NATS health
curl -s http://127.0.0.1:8222/varz | python3 -m json.tool | grep -E '"connections"|"version"'

# List Podman secrets
podman secret ls

# Verify secret is readable in a container
podman exec lyra-hub cat /run/secrets/lyra-nats-hub.seed | head -c 4

# Auto-update status
podman auto-update --dry-run
systemctl --user status podman-auto-update.timer
```

## Common issues

| Symptom | Likely cause | Fix |
|---|---|---|
| `lyra-hub` fails to start | Missing `lyra-nats-hub` secret | `./deploy/install.sh --secrets-only` |
| NATS auth failure in logs | Stale auth.conf | `make nats-regen-authconf` + restart NATS |
| `lyra-gh-helper` fails | Missing `lyra-gh-pem` | Install PEM secret (see above) |
| Container restart loop | `RestartSec=10` applies — check logs | `journalctl --user -u <svc> -n 50` |
| Auto-update not pulling | Timer inactive | `systemctl --user start podman-auto-update.timer` |

## Pitfall: double-quotes in HealthCmd= are dropped by the Quadlet generator

The Quadlet generator silently drops the closing double-quote from `HealthCmd=` values,
turning `HealthCmd=pgrep -f "lyra adapter X"` into the JSON array
`["CMD-SHELL","pgrep -f \"lyra adapter X"]` — note the missing closing `"`.
`/bin/sh` then fails with `Syntax error: Unterminated quoted string`, and the container
reports `unhealthy` indefinitely (all 6 `lyra-*` units were affected until issue #1370).

Workaround: use `/proc/1/cmdline` — no quotes required, and `grep`/`cat` are present in
both the slim (`:staging-svc`) and fat (`:staging`) images (unlike `pgrep` which requires
`procps`):

```ini
# correct — no quotes, no procps dependency
HealthCmd=grep -q telegram /proc/1/cmdline
```

The `quadlet-lint.yml` CI workflow enforces this via a `grep -nP 'HealthCmd=.*"'` check that
fails on any double-quote in a `HealthCmd=` line. See issue #1370.

## Outbound audio durable delivery (#1482)

Runbook for deploying and rolling back the JetStream-backed outbound audio path
introduced by issue #1482. The audio path uses a dedicated 5-token subject family
(`lyra.outbound.audio.<platform>.<bot_id>`), JetStream stream `LYRA_OUTBOUND_AUDIO`,
and KV dedup bucket `lyra_outbound_audio_sent`. See ADR-077 for the full decision record.

### Stream + consumer parameters

| Parameter | Value | Source |
|---|---|---|
| Stream | `LYRA_OUTBOUND_AUDIO` | `stream_setup.py` |
| Subjects | `lyra.outbound.audio.>` | `stream_setup.py` |
| Retention | Limits (NOT WorkQueue — multi-consumer fan-out) | `stream_setup.py` |
| MaxAge | 24 h | `stream_setup.py` |
| MaxBytes | 32 MiB | `stream_setup.py` |
| Consumer durable | `outbound-audio-telegram`, `outbound-audio-discord` | `audio_consumer_bootstrap.py` |
| Filter subject | `lyra.outbound.audio.<platform>.>` | `audio_consumer_bootstrap.py` |
| AckWait | 90 s | `stream_setup.py` |
| MaxDeliver | 5 | `stream_setup.py` |
| KV bucket | `lyra_outbound_audio_sent` | `stream_setup.py` |
| KV TTL | 900 s (15 min — ≥2× AckWait×MaxDeliver floor of 450 s) | `stream_setup.py` |

### Deploy order (strict — each step is a prerequisite for the next)

**Step 1 — ACL: update `acl-matrix.json` → regen `auth.conf` → RESTART `lyra-nats`**

```bash
# On M₁ (roxabituwer) — regen auth.conf from real nkey seeds in ~/.lyra/nkeys/
sudo env "PATH=$PATH" lyra-acl genkeys --regen-authconf

# Re-install the NATS auth secret from the regenerated file
podman secret create --replace lyra-nats-auth ~/.lyra/nkeys/auth.conf

# RESTART lyra-nats — a HUP is NOT sufficient
# auth.conf is a type=mount secret (tmpfs-backed); the in-container file is bound
# at container init only. --replace updates the Podman store but the running
# container still reads the old file. A full restart is mandatory.
systemctl --user restart lyra-nats
```

**Why ACL must precede adapters:** the adapters call `ensure_stream`, `ensure_consumer`,
and `ensure_kv` on boot (idempotent, via `audio_consumer_bootstrap.start_audio_consumer`).
These issue `$JS.API.STREAM.CREATE.LYRA_OUTBOUND_AUDIO`, `$JS.API.CONSUMER.CREATE.*`,
and KV API calls. Without the new grants in `auth.conf`, NATS returns a permission-denied
error and the adapter crashes before completing bootstrap.

**Step 2 — Deploy hub** (now publishes audio via JetStream + PubAck instead of Core fire-and-forget)

```bash
systemctl --user restart lyra-hub
```

The hub publishes to `lyra.outbound.audio.<platform>.<bot_id>` and returns immediately
(stateless, Model A). No stream provisioning is performed by the hub — that is owned
entirely by the adapters.

**Step 3 — Deploy adapters** (auto-provision stream/consumer/KV on boot, then start the pull consumer)

```bash
systemctl --user restart lyra-telegram lyra-discord
```

On boot each adapter calls (in order):
1. `ensure_stream(js)` — create or update `LYRA_OUTBOUND_AUDIO` (idempotent)
2. `ensure_kv(js)` — create or bind KV bucket `lyra_outbound_audio_sent` (idempotent)
3. `ensure_consumer(js, durable="outbound-audio-<platform>", filter_subject=…)` (idempotent)
4. `JetStreamAudioConsumer.start()` — begins the pull-subscribe loop

All three `ensure_*` calls are idempotent — safe to re-run on any subsequent restart.

> **No separate stream-creation step is required.** The adapters self-provision on start.
> The only prerequisite is that the ACL grants exist (Step 1), otherwise the JetStream API
> calls are denied before the stream can be created.

### Verification after deploy

```bash
# Confirm the stream exists and has the expected config
nats stream info LYRA_OUTBOUND_AUDIO

# Confirm both per-platform consumers exist
nats consumer info LYRA_OUTBOUND_AUDIO outbound-audio-telegram
nats consumer info LYRA_OUTBOUND_AUDIO outbound-audio-discord

# Confirm adapters are healthy (NRestarts=0 expected)
systemctl --user status lyra-telegram lyra-discord

# Adapter logs — audio delivery failures surface here, NOT in lyra-nats.service
journalctl --user -u lyra-telegram -n 50 | grep -E "audio|LYRA_OUTBOUND"
journalctl --user -u lyra-discord  -n 50 | grep -E "audio|LYRA_OUTBOUND"

# Check NATS HTTP monitoring for consumer lag and stream usage
# (thresholds: num_pending > 50 → lag warning; used > 80% of 32 MiB → fullness warning)
curl -s "http://127.0.0.1:8222/jsz?consumers=1&name=LYRA_OUTBOUND_AUDIO" \
  | python3 -m json.tool | grep -E '"num_pending"|"name"|"bytes"'
```

The monitoring subsystem (`python -m lyra.monitoring`) runs two audio-specific probes after
this deploy:

| Check name | What it monitors | Fail condition |
|---|---|---|
| `audio:consumer_lag` | `num_pending` per `outbound-audio-*` consumer | `num_pending > 50` or oldest unacked message age > 20 h |
| `audio:stream_usage` | `LYRA_OUTBOUND_AUDIO` bytes vs 32 MiB max | used > 80 % of max |

### Rollback

Rollback consists of reverting the hub to at-most-once audio publish and stopping the audio
consumers. The ACL grants and JetStream resources can be left in place — they are additive
and harmless.

**Step R1 — Revert hub to prior image or prior code**

```bash
# Point hub back to the previous image tag and restart
# (edit Image= in lyra-hub.container, or make quadlet-install with prior tag)
systemctl --user restart lyra-hub
```

The reverted hub stops publishing to `lyra.outbound.audio.>`. Any messages already in
`LYRA_OUTBOUND_AUDIO` will age out after 24 h (the stream MaxAge). They will not be
delivered because the adapters' pull consumers are stopped in the next step.

**Step R2 — Stop audio consumers (revert adapter images or restart without audio bootstrap)**

```bash
systemctl --user restart lyra-telegram lyra-discord
```

If the adapter image is also rolled back to a version without `JetStreamAudioConsumer`,
the `ensure_*` calls and the pull-subscribe loop are absent — adapters start cleanly on
the legacy Core path.

**Stream and KV: leave in place or purge**

Leaving `LYRA_OUTBOUND_AUDIO` and `lyra_outbound_audio_sent` in place is safe — they are
inactive once the adapters stop consuming. Messages age out at MaxAge=24 h; KV keys at TTL=900 s.

To purge explicitly (optional, operator-driven):

```bash
nats stream rm LYRA_OUTBOUND_AUDIO --force
# KV bucket is a stream internally; removing the stream also drops the KV bucket
nats stream rm KV_lyra_outbound_audio_sent --force
```

**ACL grants: safe to leave**

The `lyra.outbound.audio.>` publish grant (hub) and the adapter JetStream API grants are
additive. Leaving them in `auth.conf` has no functional impact when the audio code path is
inactive. Remove them only if a full ACL audit is underway (requires regen + `lyra-nats` RESTART).

> **Note:** in-flight messages in `LYRA_OUTBOUND_AUDIO` at the time of hub rollback will
> not be delivered — the hub no longer publishes, and the adapters have stopped consuming.
> These messages age out within 24 h. This is acceptable: the rollback scenario implies a
> confirmed regression; undelivered audio from the failed window is intentionally dropped.

### Cross-references

- ADR-077 — `lyra.outbound.audio.*` subject naming + JetStream design decision
- `deploy/nats/acl-matrix.json` — full ACL grant matrix (hub + telegram-adapter + discord-adapter identities)
- `src/lyra/infrastructure/outbound_audio/stream_setup.py` — stream/consumer/KV config constants
- `src/lyra/bootstrap/standalone/audio_consumer_bootstrap.py` — adapter boot sequence
- `src/lyra/monitoring/checks_audio.py` — `audio:consumer_lag` + `audio:stream_usage` probes
- `deploy/CLAUDE.md` — `type=mount` secret restart requirement (general invariant)

## References

- `deploy/quadlet/` — unit files (authoritative)
- `deploy/quadlet.toml` — component manifest
- `deploy/install.sh` — idempotent install script
- `docs/DEPLOYMENT.md` — full deployment guide
- `~/projects/docs/container-deployment-standard.md` — 18 standards SSoT
