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

# 2. Run idempotent install
cd ~/projects/lyra
./deploy/install.sh

# 3. Start services
systemctl --user start lyra-nats lyra-hub lyra-telegram lyra-discord lyra-clipool lyra-gh-helper

# 4. Verify
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

The BlobStore consists of two parts that must be snapshotted in order: the SQLite index
(`~/.lyra/blobstore/index.sqlite`) first, then the content-addressed shard tree
(`~/.lyra/blobstore/sha256/`). Reversing the order risks capturing a `blob_refs` row
whose shard file was not yet in the snapshot — a phantom row at restore time.

`FsBlobStore` uses a per-instance `asyncio.Lock`, not a global write-quiesce. A concurrent
`put()` between step 1 and step 2 can leave a shard file in the snapshot whose `blob_refs`
row was NOT captured in the DB snapshot. The restore invariant handles this safely — see
`## Restore invariant` below.

1. Snapshot the SQLite index (atomic per the SQLite `.backup` API):
   ```bash
   mkdir -p /tmp/blobstore-snapshot
   sqlite3 ~/.lyra/blobstore/index.sqlite ".backup '/tmp/blobstore-snapshot/index.sqlite'"
   ```

2. Snapshot the shard tree together with the DB snapshot (use hardlinks to minimise disk usage):
   ```bash
   cp -al ~/.lyra/blobstore/sha256 /tmp/blobstore-snapshot/sha256
   ```
   For off-host backup, pipe through Restic or similar:
   ```bash
   tar -cf - /tmp/blobstore-snapshot | restic backup --stdin --stdin-filename blobstore.tar
   ```

3. Verify the snapshot (should report 0 mismatches):
   ```bash
   sha256sum -c <(find /tmp/blobstore-snapshot/sha256 -type f -exec sha256sum {} +)
   ```

Recommended cadence: daily, scheduled when traffic is low. Store alongside `~/.lyra/config.db`
backups.

## Restore invariant

After extracting a backup, the SQLite manifest is the authoritative record. Any shard file
in `sha256/` that is NOT referenced by a `blobs.store_path` row is a content-addressed orphan
produced by a `put()` that wrote the file but did not complete its `INSERT blob_refs` before
the DB snapshot was taken. These orphans are always safe to discard — no consumer ever held
a `store_key` pointing to them.

Reconciliation procedure after restore:

1. List expected files (from the DB):
   ```bash
   sqlite3 index.sqlite "SELECT store_path FROM blobs"
   ```

2. List actual files on disk:
   ```bash
   find sha256 -type f
   ```

3. Discard files in (2) that are absent from (1) — they are dedup-orphans, never user data loss.

The reverse case — a `blob_refs` row pointing to a missing shard file — is the unrecoverable
bug that the step-1-before-step-2 ordering prevents.

→ See `docs/architecture/storage.md` (BlobStore section) for the write-durability invariant
that underpins this restore procedure.

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

## References

- `deploy/quadlet/` — unit files (authoritative)
- `deploy/quadlet.toml` — component manifest
- `deploy/install.sh` — idempotent install script
- `docs/DEPLOYMENT.md` — full deployment guide
- `~/projects/docs/container-deployment-standard.md` — 18 standards SSoT
