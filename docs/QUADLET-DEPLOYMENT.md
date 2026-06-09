# Quadlet Deployment — Lyra

Runbook for installing, operating, and rotating secrets in the Lyra Quadlet deployment on M₁ (`roxabituwer`).

→ SSoT standards: `~/projects/docs/container-deployment-standard.md`
→ Component manifest: `deploy/quadlet.toml`
→ Idempotent install script: `deploy/install.sh`

> **Deployment-state caveat (2026-06).** M₁ (`roxabituwer`) currently runs the **legacy `lyra-*` infra** — units `lyra-*`, secrets `lyra-nats-*` / `lyra-bot-*`, data dir `~/.lyra`, repo `~/projects/lyra`, image `ghcr.io/roxabi/lyra:staging{,-svc}`. The `factory-*` names throughout this runbook are the **post-Phase-2 infra-rename target**; that migration (data move `~/.lyra`→`~/.roxabi/factory`, secret + unit + image-repo rename) is **pending**, tracked in **#1710** (combined with the #1670 `factory.*` wire cutover). Until it lands, mentally translate `factory-*`→`lyra-*` when operating M₁.

## Architecture

Eight containers on `roxabi.network` (systemd `--user`, linger enabled):

| Service | Container | Role |
|---|---|---|
| `factory-nats` | NATS 2.x | Message bus (port 4222) |
| `factory-hub` | lyra | Hub — routing, pool, memory |
| `factory-telegram` | lyra | Telegram adapter |
| `factory-discord` | lyra | Discord adapter |
| `factory-clipool` | lyra | CliPool NATS worker (Claude subprocesses) |
| `factory-gh-helper` | lyra | GitHub App token-mint helper (`factory-gh.pod`) |
| `factory-turn-writer` | lyra | JetStream subscriber-writer for turns.db (#1331) |
| `factory-blobstore` | lyra | HTTP-fronted BlobStore service (port 8449, #1330) |

## Install

### First-time setup

```bash
# 1. Generate nkeys + auth.conf (if not done yet)
cd ~/projects/roxabi-factory
make nats-setup

# 2. Install secrets + static units
cd ~/projects/roxabi-factory
./deploy/install.sh

# 3. Seed BotStore + render adapter templates + daemon-reload
#    Required since #1416: hub bootstraps auth from BotStore (~/.roxabi/factory/config.db),
#    not from config.toml directly. Skipping this step causes a hub crash-loop.
make quadlet-install

# 4. Start services
systemctl --user start factory-nats factory-hub factory-telegram factory-discord factory-clipool factory-gh-helper

# 5. Provision JetStream monitoring streams (idempotent)
uv run python deploy/nats/bootstrap_streams.py

# 6. Verify
systemctl --user status 'factory-*'
podman ps
```

### Re-deploy after code change

Auto-update handles this for GHCR images. Manual:

```bash
cd ~/projects/roxabi-factory
make quadlet-install    # copy units + daemon-reload
# then restart affected services
systemctl --user restart factory-hub factory-telegram factory-discord factory-clipool
```

## One-time migration: discord.db → private named volume (#1721)

Issue #1721 moves `discord.db` from the hub-shared `factory-data.volume` to a
Discord-private named volume `factory-discord-data` (podman-managed, mounted at
`/home/factory/.roxabi/factory/discord` inside the Discord container).
Before the converge that applies these changes, run the steps below on M₁ to
preserve active thread→session links. The new volume starts empty; missing the
copy means the Discord adapter cold-starts with an empty ThreadStore (all active
thread sessions lost).

### Step A — Copy discord.db into the named volume before the converge

The old `discord.db` is at `~/.roxabi/factory/discord.db` (inside the shared hub bind).
The new home is the named volume `factory-discord-data`, which podman creates on first
use. Copy the file in now using a temporary container that mounts both source and target:

```bash
# On M₁ (roxabituwer), before running make converge / make quadlet-install:
# Stop the Discord adapter if running (safe — hub continues independently):
systemctl --user stop factory-discord || true

# Create the named volume if not yet created, then copy discord.db into it:
podman run --rm \
  -v factory-discord-data:/dst \
  -v ~/.roxabi/factory:/src:ro \
  alpine cp /src/discord.db /dst/discord.db
```

Verify the copy landed in the volume:

```bash
podman run --rm -v factory-discord-data:/data:ro alpine \
  sh -c 'ls -lh /data/discord.db && echo OK'
```

### Step B — Converge

```bash
make converge
```

The converge installs the updated `factory-discord-data.volume` unit, renders the updated
`factory-discord.container` (which now mounts `factory-discord-data` at
`~/.roxabi/factory/discord`), daemon-reloads, and restarts all services.

### Step C — Verify the Discord adapter is healthy

```bash
systemctl --user status factory-discord
# Expected: Active: active (running), NRestarts=0
journalctl --user -u factory-discord -n 20 | grep -E "ThreadStore|discord\.db|error" || true
```

Confirm Discord mounts the named volume (not the hub's factory-data.volume):

```bash
# Should show factory-discord-data.volume at ~/.roxabi/factory/discord
podman inspect factory-discord --format '{{range .Mounts}}{{.Name}} → {{.Destination}}{{"\n"}}{{end}}' | grep discord || true
```

Confirm Telegram no longer mounts any data volume:

```bash
# Should print nothing (no factory-data.volume mount in telegram unit)
grep "factory-data.volume" ~/.config/containers/systemd/factory-telegram.container || echo "OK — no shared volume"
```

### Step D — Verify no adapter holds turns.db open (epic #1049 AC#2)

After Step C confirms a healthy converge, verify that no adapter process holds `turns.db`
open. Post-migration the canonical `turns.db` at `~/.roxabi/factory/turns.db` is **kept** —
it is exclusively written by `factory-turn-writer` and read by `factory-hub` (D5 / ADR-075).
Epic #1049 AC#2 is satisfied by confirming the adapters no longer open it (they now resolve
last-session via `turns.db` path-3 (`get_last_session`), **not** by deleting the hub's store.
Only a stale adapter-side copy at a *separate legacy location*, if one exists, should be removed.

> **Only delete the file if you have confirmed** that the turn-writer service is healthy
> and turns are flowing (`journalctl --user -u factory-turn-writer -n 20`). Do NOT delete
> if the turn-writer shows errors.

```bash
# Confirm turn-writer is healthy first
systemctl --user status factory-turn-writer
journalctl --user -u factory-turn-writer -n 20

# ONLY after confirming healthy — remove the legacy adapter-side turns.db if it exists
# at a separate legacy location. The canonical turns.db at ~/.roxabi/factory/turns.db
# is kept (hub reads it; turn-writer writes it).
# Epic #1049 AC#2 refers to verifying no adapter process holds turns.db open:
lsof ~/.roxabi/factory/turns.db 2>/dev/null || echo "No process has turns.db open (expected)"
```

Epic #1049 may be closed only after AC#3 (keyring.key) and AC#4 (config.db) are also
verified (see spec D10). Do NOT auto-close the epic on merge.

## Auto-sync for Quadlet file changes

Tracked Quadlet files (`deploy/quadlet/**`, Makefile, `tools/render_quadlet.py`) are
auto-converged on M₁ by `factory-quadlet-sync.timer`:

| Path | Cadence | What it does |
|---|---|---|
| **Auto** (`factory-quadlet-sync.timer`) | Every 5 min | `git fetch` → `ff-only pull` → diff-guard → conditional `make quadlet-install` |
| **Manual** (`make quadlet-install`) | Operator-driven | Immediate — use when you need a change NOW or the timer is disabled |

### First-time enable

Run once (idempotent):
```bash
cd ~/projects/roxabi-factory
make quadlet-sync-install
```

### Checking sync status

```bash
# Last run output
journalctl --user -u factory-quadlet-sync -n 20

# Timer next trigger
systemctl --user list-timers factory-quadlet-sync.timer
```

### When to use manual `make quadlet-install`

- You need a Quadlet change applied **immediately** (timer max latency = 5 min).
- You are debugging a unit and want to iterate fast.
- The auto-sync service is temporarily stopped for maintenance.
- The change is a hot-fix and you cannot wait for the next timer firing.

## Secret layout

| Podman secret | Source file | Mounted at |
|---|---|---|
| `factory-nats-auth` | `~/.roxabi/factory/nkeys/auth.conf` | `/etc/nats/nkeys/auth.conf` (in factory-nats) |
| `factory-nats-hub` | `~/.roxabi/factory/nkeys/hub.seed` | `/run/secrets/factory-nats-hub.seed` |
| `factory-nats-telegram` | `~/.roxabi/factory/nkeys/telegram-adapter.seed` | `/run/secrets/factory-nats-telegram.seed` |
| `factory-nats-discord` | `~/.roxabi/factory/nkeys/discord-adapter.seed` | `/run/secrets/factory-nats-discord.seed` |
| `factory-nats-clipool` | `~/.roxabi/factory/nkeys/clipool-worker.seed` | `/run/secrets/factory-nats-clipool.seed` |
| `factory-gh-pem` | `~/.roxabi/factory/gh-app.pem` | `/run/secrets/gh-app.pem` (helper only) |
| `factory-claude-oauth` | `~/.roxabi/factory/claude-oauth.tok` | `CLAUDE_CODE_OAUTH_TOKEN` env (clipool) |
| `factory_blobstore_token` | `~/.roxabi/factory/blobstore.tok` | `/run/secrets/factory_blobstore_token` (uid=1500, gid=1500, mode=0400) |

All seed secrets use `type=mount` (tmpfs-backed). `factory-claude-oauth` uses `type=env`. (S7, S18)
`factory_blobstore_token` uses `type=mount`; the bearer token is read once at container startup by
the auth middleware and never re-read until the container restarts (ADR-054).

## JetStream streams

| Stream | Subjects | Retention | MaxAge | MaxBytes |
|---|---|---|---|---|
| `factory-events` | `factory.event.>` | Limits | 24 h (hot) | 512 MiB |
| `factory-metrics` | `factory.metric.>` | Limits | 7 d (warm) | 256 MiB |

Both streams use `StorageType.FILE` backed by `factory-jetstream.volume` (`~/.roxabi/factory/nats/jetstream`).
Provisioning is idempotent via `uv run python deploy/nats/bootstrap_streams.py` (called in first-time setup above).

Ops decision (#1183): events = 24 h hot (high churn, dashboard real-time), metrics = 7 d warm
(trending / SLA review). Both are Limits retention so multiple consumers can read the same
message; durable consumers for the future dashboard are tracked in #1035.

## Bot credentials

### (a) No manual splicing

The tracked files `deploy/quadlet/lyra-{telegram,discord}.container.tmpl` are **pure templates** — they contain a `{{bot_secrets}}` marker and zero `Secret=factory-bot-*` lines. Per-bot `Secret=factory-bot-<platform>-<bot_id>,…` directives are generated at install time by `tools/render_quadlet.py`, which reads bot rows from `BotStore` (`~/.roxabi/factory/config.db`) and substitutes the rendered block into each template before writing the live Quadlet to `~/.config/containers/systemd/`.

**Never edit `~/.config/containers/systemd/lyra-{telegram,discord}.container` directly.** Any manual change is silently overwritten on the next `make quadlet-install`. This replaces the prior fragment-paste workflow that caused the 2026-05-26 crash-loop cascade (#1369): a `git pull --ff-only` during the #1331 deploy discarded an operator-spliced `BEGIN/END` block, stranding 4 bot-token mounts and forcing 6.5 h of adapter crash-loop before re-splice. Use the onboarding flow in (b) instead.

### (b) Bot onboarding

End-to-end flow for adding a new bot:

1. Add the bot to the DB (DB-first since #1415):
   ```bash
   factory agent <platform> add <bot_id> --agent <agent>
   ```
   (Replace `<platform>` with `telegram` or `discord`.)

2. Install the Podman secret for the bot token (host-local):
   ```bash
   factory bot secret install <platform> <bot_id>
   ```
   This creates `factory-bot-<platform>-<bot_id>` in the Podman secret store. For webhook variants, also run:
   ```bash
   factory bot secret install <platform> <bot_id>-webhook
   ```

3. Render and restart:
   ```bash
   make quadlet-install
   ```
   The target runs `factory bot init` (idempotent re-sync of `config.toml` → `BotStore`), then `render_quadlet.py` reads from `BotStore`, generates the `Secret=` directives, writes the new Quadlet atomically, runs `systemctl --user daemon-reload`, then restarts the adapter container.

4. Verify the adapter is healthy:
   ```bash
   systemctl --user status factory-<platform>
   ```
   Expected: `Active: active (running)` with `NRestarts=0`.

> **Why `systemctl --user restart` is mandatory after a `Secret=` change:** `systemctl daemon-reload` regenerates the transient `.service` from the new Quadlet definition but does NOT propagate `Secret=` mount changes into an already-running container. The new tmpfs mount only takes effect when the container is (re)started. `make quadlet-install` issues the restart automatically — do not skip it.

### (c) CI guard

Three artefacts prevent re-introduction of the manual-splice pattern:

- `tools/check_quadlet_template_purity.sh` — enforces three invariants on all tracked `deploy/quadlet/*.container.tmpl` files: (i) no `Secret=factory-bot-*` lines present; (ii) no `# --- BEGIN … ---` splice-block markers present; (iii) `tools/render_quadlet.py` exists (guards against accidental deletion of the render script).
- `make quadlet-lint` — local entry point; run before pushing to catch purity failures early.
- `.github/workflows/quadlet-lint.yml` — CI job that runs the purity check on every PR touching `deploy/quadlet/**`. A PR that reintroduces a `Secret=factory-bot-` line or a `BEGIN/END` splice block into any `.container.tmpl` will fail here.

### (d) Multi-host caveat

`~/.roxabi/factory/config.toml` is Syncthing-synced across M₁, M₂, and laptop. That means `[[auth.telegram_bots]]` and `[[auth.discord_bots]]` enumerate **all bots across all hosts** in one shared file. `make quadlet-install` runs `factory bot init` first (seeds `BotStore` from `config.toml`), then `render_quadlet.py` reads from `BotStore` — so the rendered Quadlet on every host includes `Secret=` lines for every configured bot.

However, Podman secrets (`factory-bot-<platform>-<bot_id>`) are **host-local** and must be installed per host. A mismatch — `config.toml` lists a bot but its Podman secret is absent — causes `systemctl --user start factory-<platform>` to fail immediately:

```
Error: looking up secret name "factory-bot-telegram-<bot_id>": no such secret
```

**Mitigation:** When adding a bot, run `factory bot secret install <platform> <bot_id>` on **every host that runs the adapter for that platform** before executing `make quadlet-install`.

Current host-role mapping (from `~/projects/hosts.toml`):

| Host | Role | Adapter units |
|---|---|---|
| M₁ `roxabituwer` | `factory-hub` | `factory-telegram`, `factory-discord` (all 4 bots) |
| M₂ `roxabitower` | `image-worker` | none today |

## Secret rotation

### Rotate an nkey seed

```bash
# 1. Generate new seed
cd ~/projects/roxabi-factory
make nats-regen-authconf    # re-renders auth.conf; auto-creates missing seeds

# 2. Re-install the affected secret
podman secret create --replace factory-nats-hub ~/.roxabi/factory/nkeys/hub.seed

# 3. Reload NATS (auth.conf change), then restart the affected container
systemctl --user restart factory-nats
systemctl --user restart factory-hub
```

### Rotate all nkey secrets at once

```bash
cd ~/projects/roxabi-factory
./deploy/install.sh --force --secrets-only
systemctl --user restart factory-nats factory-hub factory-telegram factory-discord factory-clipool
```

### Rotate GitHub App PEM

```bash
# Copy new PEM to ~/.roxabi/factory/gh-app.pem, then:
podman secret create --replace factory-gh-pem ~/.roxabi/factory/gh-app.pem
systemctl --user restart factory-gh-helper
```

### Rotating the BlobStore bearer token

`factory_blobstore_token` is a `type=mount` secret — the tmpfs file is bound at container init.
`podman secret create --replace` alone does NOT refresh the in-container file; a restart is
mandatory (ADR-054). Keep the prior source file as `.prev` until rotation is confirmed.

1. Write the new token to the host source file:
   ```bash
   printf '%s' "$NEW_TOK" > ~/.roxabi/factory/blobstore.tok && chmod 0600 ~/.roxabi/factory/blobstore.tok
   ```
   Keeping the source file at `~/.roxabi/factory/blobstore.tok` ensures reinstall scripts remain idempotent.

2. Update the Podman secret store:
   ```bash
   podman secret create --replace factory_blobstore_token ~/.roxabi/factory/blobstore.tok
   ```

3. Restart the service so the new tmpfs file is bound at container init:
   ```bash
   systemctl --user restart factory-blobstore.service
   ```

4. Verify the new token is in place (first 8 chars should match `$NEW_TOK`):
   ```bash
   podman exec factory-blobstore sh -c 'head -c 8 /run/secrets/factory_blobstore_token'
   ```

5. Rollback (if step 4 fails — keep `.prev` until this step is confirmed unnecessary):
   ```bash
   podman secret create --replace factory_blobstore_token ~/.roxabi/factory/blobstore.tok.prev
   systemctl --user restart factory-blobstore.service
   ```

## Backing up the BlobStore

> **Host vs container path:** `~/.roxabi/factory/blobstore` is both the canonical host path and the container view (bind-mounted via `Volume=%h/.roxabi/factory/blobstore:/home/factory/.roxabi/factory/blobstore:z`). All backup and restore commands below reference this path.

The BlobStore consists of two parts that must be snapshotted in order: the SQLite index
(`~/.roxabi/factory/blobstore/index.sqlite`) first, then the content-addressed shard tree
(`~/.roxabi/factory/blobstore/`). Reversing the order risks capturing a `blob_refs` row
whose shard file was not yet in the snapshot — a phantom row at restore time.

`FsBlobStore` uses a per-instance `asyncio.Lock`, not a global write-quiesce. A concurrent
`put()` between step 1 and step 2 can leave a shard file in the snapshot whose `blob_refs`
row was NOT captured in the DB snapshot. The restore invariant handles this safely — see
`## Restore invariant` below.

1. Snapshot the SQLite index (atomic per the SQLite `.backup` API):
   ```bash
   mkdir -p /tmp/blobstore-snapshot
   sqlite3 ~/.roxabi/factory/blobstore/index.sqlite ".backup '/tmp/blobstore-snapshot/index.sqlite'"
   ```

2. Snapshot the shard tree together with the DB snapshot (use hardlinks to minimise disk usage):
   ```bash
   cp -al ~/.roxabi/factory/blobstore /tmp/blobstore-snapshot/blobs
   ```
   For off-host backup, pipe through Restic or similar:
   ```bash
   tar -cf - /tmp/blobstore-snapshot | restic backup --stdin --stdin-filename blobstore.tar
   ```

3. Verify the snapshot (should report 0 mismatches):
   ```bash
   sha256sum -c <(find /tmp/blobstore-snapshot/blobs -type f -exec sha256sum {} +)
   ```

Recommended cadence: daily, scheduled when traffic is low. Store alongside `~/.roxabi/factory/config.db`
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

## Diagnostic

```bash
# Service status
systemctl --user status 'factory-*'

# Running containers
podman ps --filter 'name=lyra'

# Logs
journalctl --user -u factory-hub -f
journalctl --user -u factory-telegram -f
journalctl --user -u factory-clipool -f
journalctl --user -u factory-gh-helper -f

# NATS health
curl -s http://127.0.0.1:8222/varz | python3 -m json.tool | grep -E '"connections"|"version"'

# List Podman secrets
podman secret ls

# Verify secret is readable in a container
podman exec factory-hub cat /run/secrets/factory-nats-hub.seed | head -c 4

# Auto-update status
podman auto-update --dry-run
systemctl --user status podman-auto-update.timer
```

## Common issues

| Symptom | Likely cause | Fix |
|---|---|---|
| `factory-hub` fails to start | Missing `factory-nats-hub` secret | `./deploy/install.sh --secrets-only` |
| NATS auth failure in logs | Stale auth.conf | `make nats-regen-authconf` + restart NATS |
| `factory-gh-helper` fails | Missing `factory-gh-pem` | Install PEM secret (see above) |
| Container restart loop | `RestartSec=10` applies — check logs | `journalctl --user -u <svc> -n 50` |
| Auto-update not pulling | Timer inactive or misconfigured | See M₁ auto-update remediation runbook below |

## M₁ auto-update remediation runbook

Run when `podman auto-update` has stopped pulling new images (all three timers must be
enabled and active for fully hands-off deploys):

### 1. Re-install the drop-in + enable the quadlet-sync timer

```bash
cd ~/projects/roxabi-factory
make quadlet-sync-install   # installs drop-in + systemctl --user enable --now factory-quadlet-sync.timer
```

### 2. Enable/start the three timers

Run `daemon-reload` first so the drop-in override is loaded before the timers are activated. Then restart `podman-auto-update.timer` so any changed `OnCalendar=` schedule takes effect on an already-active timer.

```bash
systemctl --user daemon-reload
systemctl --user enable --now podman-auto-update.timer
systemctl --user enable --now factory-quadlet-sync.timer
systemctl --user enable --now factory-post-autoupdate.timer
systemctl --user restart podman-auto-update.timer
```

### 3. Verify timer state

Check all three timers are enabled and active:

```bash
systemctl --user is-enabled podman-auto-update.timer factory-quadlet-sync.timer factory-post-autoupdate.timer
systemctl --user is-active  podman-auto-update.timer factory-quadlet-sync.timer factory-post-autoupdate.timer
```

Expected output: three lines of `enabled` then three lines of `active`.

### 4. Verify registry tracking

```bash
podman auto-update --dry-run
```

Expected: 7 containers listed with `registry` tracking (`factory-hub`, `factory-telegram`,
`factory-discord`, `factory-clipool`, `factory-gh-helper`, `factory-turn-writer`,
`factory-blobstore`). `factory-nats` must NOT appear — it is pinned by digest with no
autoupdate label.

### 5. Record output on issue #1729

Copy the output of `podman auto-update --dry-run` and the three `is-enabled`/`is-active`
results as a comment on issue #1729. This is the SC9 prod-green verification check.

## Pitfall: double-quotes in HealthCmd= are dropped by the Quadlet generator

The Quadlet generator silently drops the closing double-quote from `HealthCmd=` values,
turning `HealthCmd=pgrep -f "factory adapter X"` into the JSON array
`["CMD-SHELL","pgrep -f \"factory adapter X"]` — note the missing closing `"`.
`/bin/sh` then fails with `Syntax error: Unterminated quoted string`, and the container
reports `unhealthy` indefinitely (all 6 `factory-*` units were affected until issue #1370).

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
(`factory.outbound.audio.<platform>.<bot_id>`), JetStream stream `FACTORY_OUTBOUND_AUDIO`,
and KV dedup bucket `factory_outbound_audio_sent`. See ADR-077 for the full decision record.

### Stream + consumer parameters

| Parameter | Value | Source |
|---|---|---|
| Stream | `FACTORY_OUTBOUND_AUDIO` | `stream_setup.py` |
| Subjects | `factory.outbound.audio.>` | `stream_setup.py` |
| Retention | Limits (NOT WorkQueue — multi-consumer fan-out) | `stream_setup.py` |
| MaxAge | 24 h | `stream_setup.py` |
| MaxBytes | 32 MiB | `stream_setup.py` |
| Consumer durable | `outbound-audio-telegram`, `outbound-audio-discord` | `audio_consumer_bootstrap.py` |
| Filter subject | `factory.outbound.audio.<platform>.>` | `audio_consumer_bootstrap.py` |
| AckWait | 90 s | `stream_setup.py` |
| MaxDeliver | 5 | `stream_setup.py` |
| KV bucket | `factory_outbound_audio_sent` | `stream_setup.py` |
| KV TTL | 900 s (15 min — ≥2× AckWait×MaxDeliver floor of 450 s) | `stream_setup.py` |

### Deploy order (strict — each step is a prerequisite for the next)

**Step 1 — ACL: update `acl-matrix.json` → regen `auth.conf` → RESTART `factory-nats`**

```bash
# On M₁ (roxabituwer) — regen auth.conf from real nkey seeds in ~/.roxabi/factory/nkeys/
sudo env "PATH=$PATH" factory-acl genkeys --regen-authconf

# Re-install the NATS auth secret from the regenerated file
podman secret create --replace factory-nats-auth ~/.roxabi/factory/nkeys/auth.conf

# RESTART factory-nats — a HUP is NOT sufficient
# auth.conf is a type=mount secret (tmpfs-backed); the in-container file is bound
# at container init only. --replace updates the Podman store but the running
# container still reads the old file. A full restart is mandatory.
systemctl --user restart factory-nats
```

**Why ACL must precede adapters:** the adapters call `ensure_stream`, `ensure_consumer`,
and `ensure_kv` on boot (idempotent, via `audio_consumer_bootstrap.start_audio_consumer`).
These issue `$JS.API.STREAM.CREATE.FACTORY_OUTBOUND_AUDIO`, `$JS.API.CONSUMER.CREATE.*`,
and KV API calls. Without the new grants in `auth.conf`, NATS returns a permission-denied
error and the adapter crashes before completing bootstrap.

**Step 2 — Deploy hub** (now publishes audio via JetStream + PubAck instead of Core fire-and-forget)

```bash
systemctl --user restart factory-hub
```

The hub publishes to `factory.outbound.audio.<platform>.<bot_id>` and returns immediately
(stateless, Model A). No stream provisioning is performed by the hub — that is owned
entirely by the adapters.

**Step 3 — Deploy adapters** (auto-provision stream/consumer/KV on boot, then start the pull consumer)

```bash
systemctl --user restart factory-telegram factory-discord
```

On boot each adapter calls (in order):
1. `ensure_stream(js)` — create or update `FACTORY_OUTBOUND_AUDIO` (idempotent)
2. `ensure_kv(js)` — create or bind KV bucket `factory_outbound_audio_sent` (idempotent)
3. `ensure_consumer(js, durable="outbound-audio-<platform>", filter_subject=…)` (idempotent)
4. `JetStreamAudioConsumer.start()` — begins the pull-subscribe loop

All three `ensure_*` calls are idempotent — safe to re-run on any subsequent restart.

> **No separate stream-creation step is required.** The adapters self-provision on start.
> The only prerequisite is that the ACL grants exist (Step 1), otherwise the JetStream API
> calls are denied before the stream can be created.

### Verification after deploy

```bash
# Confirm the stream exists and has the expected config
nats stream info FACTORY_OUTBOUND_AUDIO

# Confirm both per-platform consumers exist
nats consumer info FACTORY_OUTBOUND_AUDIO outbound-audio-telegram
nats consumer info FACTORY_OUTBOUND_AUDIO outbound-audio-discord

# Confirm adapters are healthy (NRestarts=0 expected)
systemctl --user status factory-telegram factory-discord

# Adapter logs — audio delivery failures surface here, NOT in factory-nats.service
journalctl --user -u factory-telegram -n 50 | grep -E "audio|FACTORY_OUTBOUND"
journalctl --user -u factory-discord  -n 50 | grep -E "audio|FACTORY_OUTBOUND"

# Check NATS HTTP monitoring for consumer lag and stream usage
# (thresholds: num_pending > 50 → lag warning; used > 80% of 32 MiB → fullness warning)
curl -s "http://127.0.0.1:8222/jsz?consumers=1&name=FACTORY_OUTBOUND_AUDIO" \
  | python3 -m json.tool | grep -E '"num_pending"|"name"|"bytes"'
```

The monitoring subsystem (`python -m factory.monitoring`) runs two audio-specific probes after
this deploy:

| Check name | What it monitors | Fail condition |
|---|---|---|
| `audio:consumer_lag` | `num_pending` per `outbound-audio-*` consumer | `num_pending > 50` or oldest unacked message age > 20 h |
| `audio:stream_usage` | `FACTORY_OUTBOUND_AUDIO` bytes vs 32 MiB max | used > 80 % of max |

### Rollback

Rollback consists of reverting the hub to at-most-once audio publish and stopping the audio
consumers. The ACL grants and JetStream resources can be left in place — they are additive
and harmless.

**Step R1 — Revert hub to prior image or prior code**

```bash
# Point hub back to the previous image tag and restart
# (edit Image= in factory-hub.container, or make quadlet-install with prior tag)
systemctl --user restart factory-hub
```

The reverted hub stops publishing to `factory.outbound.audio.>`. Any messages already in
`FACTORY_OUTBOUND_AUDIO` will age out after 24 h (the stream MaxAge). They will not be
delivered because the adapters' pull consumers are stopped in the next step.

**Step R2 — Stop audio consumers (revert adapter images or restart without audio bootstrap)**

```bash
systemctl --user restart factory-telegram factory-discord
```

If the adapter image is also rolled back to a version without `JetStreamAudioConsumer`,
the `ensure_*` calls and the pull-subscribe loop are absent — adapters start cleanly on
the legacy Core path.

**Stream and KV: leave in place or purge**

Rollback to a pre-#1482 image is a config/image rollback (pin `Image=` to the prior tag per the image-lifecycle pattern in `deploy/CLAUDE.md`). The JetStream stream (`FACTORY_OUTBOUND_AUDIO`), consumers, and KV bucket (`factory_outbound_audio_sent`) persist after rollback — a pre-#1482 image simply stops consuming the audio subject; messages already on the stream remain until MaxAge (24 h) and then expire automatically. The audio path also exposes module counters `audio_terminal_drop_total` / `audio_redelivery_total` for monitoring.

Leaving `FACTORY_OUTBOUND_AUDIO` and `factory_outbound_audio_sent` in place is safe — they are
inactive once the adapters stop consuming. Messages age out at MaxAge=24 h; KV keys at TTL=900 s.

To purge explicitly (optional, operator-driven):

```bash
nats stream rm FACTORY_OUTBOUND_AUDIO --force
# KV bucket is a stream internally; removing the stream also drops the KV bucket
nats stream rm KV_factory_outbound_audio_sent --force
```

**`factory-turns-meta` KV bucket (dormant since #1777)**

The `factory-turns-meta` NATS-KV bucket was written by the deleted `KvLastSessionStore` path (path-2, removed in #1777). It is no longer written or read by any component and is safe to delete:

```bash
nats kv del factory-turns-meta
```

**ACL grants: safe to leave**

The `factory.outbound.audio.>` publish grant (hub) and the adapter JetStream API grants are
additive. Leaving them in `auth.conf` has no functional impact when the audio code path is
inactive. Remove them only if a full ACL audit is underway (requires regen + `factory-nats` RESTART).

> **Note:** in-flight messages in `FACTORY_OUTBOUND_AUDIO` at the time of hub rollback will
> not be delivered — the hub no longer publishes, and the adapters have stopped consuming.
> These messages age out within 24 h. This is acceptable: the rollback scenario implies a
> confirmed regression; undelivered audio from the failed window is intentionally dropped.

### Cross-references

- ADR-077 — `factory.outbound.audio.*` subject naming + JetStream design decision
- `deploy/nats/acl-matrix.json` — full ACL grant matrix (hub + telegram-adapter + discord-adapter identities)
- `src/factory/infrastructure/outbound_audio/stream_setup.py` — stream/consumer/KV config constants
- `src/factory/bootstrap/standalone/audio_consumer_bootstrap.py` — adapter boot sequence
- `src/factory/monitoring/checks_audio.py` — `audio:consumer_lag` + `audio:stream_usage` probes
- `deploy/CLAUDE.md` — `type=mount` secret restart requirement (general invariant)

## References

- `deploy/quadlet/` — unit files (authoritative)
- `deploy/quadlet.toml` — component manifest
- `deploy/install.sh` — idempotent install script
- `docs/DEPLOYMENT.md` — full deployment guide
- `~/projects/docs/container-deployment-standard.md` — 18 standards SSoT
