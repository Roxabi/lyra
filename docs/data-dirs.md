# Data Directories

factory stores runtime data in two root locations:

| Host path | Purpose | Container path |
|---|---|---|
| `~/.roxabi/factory/` | Application vault (databases, secrets, config, env) | `/home/factory/.roxabi/factory` (via `factory-data.volume`) |
| `~/.roxabi/factory/blobstore/` | BlobStore shard tree + SQLite index | `/home/factory/.roxabi/factory/blobstore` (via bind-mount in `factory-blobstore.container`) |

---

## `~/.roxabi/factory/` — Application vault

| Subdirectory / File | Purpose | Syncthing |
|---|---|---|
| `config.db` | Agents, bot-agent map, runtime state, credentials | **Synced** |
| `auth.db` | Auth grants, identity aliases (legacy, tombstone) | **Synced** |
| `turns.db` | Conversation turns, pool sessions | **Synced** |
| `discord.db` | Discord thread data | **Synced** |
| `message_index.db` | Message index for search/retrieval | **Synced** |
| `agents/*.toml` | Agent seed overrides (operator-local) | **Synced** |
| `config.toml` | Instance wiring (bots, tokens, auth, defaults) | **Synced** |
| `nkeys/` | NATS nkey seeds and `auth.conf` | **Synced** |
| `env/` | Quadlet env files (`hub.env`, `blobstore.env`) | **Synced** |
| `rotation-log.md` | Credential rotation audit (append-only) | **Synced** |
| `blobstore.tok` | BlobStore bearer token (SSoT for factory + voiceCLI bind-mount) | **Excluded** (per-host Podman secret materialisation — ADR-093) |
| `nats/jetstream/` | JetStream persistent storage | **Excluded** (host-local, large WAL files) |
| `blobstore/` | BlobStore shard tree + SQLite index (real directory) | **Excluded** (large binary data — see Syncthing exclusions below) |

---

## `~/.local/state/factory/` — Host-local state (not Syncthing-synced)

| Path | Purpose |
|---|---|
| `logs/operator.log` | JSONL deploy/operator actions (`install.sh`, `make converge`) |
| `loki/` | Loki chunks + index (31d retention) |
| `promtail/positions.yaml` | Promtail read offsets |

Container apps log to stdout → journald → Promtail → Loki. See [runbooks/operator-log.md](runbooks/operator-log.md) and [runbooks/loki-query.md](runbooks/loki-query.md).

---

## `~/.roxabi/factory/blobstore/` — BlobStore host path

| Subdirectory / File | Purpose |
|---|---|
| `index.sqlite` | SQLite manifest of all stored blobs |
| `<sha[:2]>/<sha>` | Content-addressed prefix-shard tree (immutable, hardlink-safe) — see SSoT below |

This directory is **excluded from Syncthing** across all hosts. It is large, append-only binary data and should be backed up via the BlobStore snapshot procedure (`docs/runbooks/blobstore-backup-restore.md`).

> **SSoT for layout:** `packages/roxabi-blobs/src/roxabi_blobs/fs_store.py` defines the exact prefix-shard algorithm (`_shard_for(content_hash) → content_hash[:2]`) and write order (`file → fsync(file) → fsync(shard dir) → INSERT blobs → INSERT blob_refs`).

---

## `~/.roxabi-vault/` — Vault CLI database

Mounted into `factory-hub` so the `vault put` subprocess can write to it. Not part of Syncthing.

---

## Syncthing exclusions

Syncthing syncs `~/.roxabi/factory/` across M₁, M₂, and laptop. The following paths are excluded:

| Path | Reason |
|---|---|
| `~/.roxabi/factory/nats/jetstream/` | Host-local JetStream WAL; large, not portable |
| `~/.roxabi/factory/blobstore/` | Large append-only binary data; high churn |
| `~/.roxabi/factory/blobstore.tok` | Per-host bearer token; voiceCLI bind-mounts factory path — syncing caused stale-copy incidents (ADR-093) |

`~/.roxabi/factory/.stignore` is maintained idempotently by `deploy/install.sh` from `deploy/templates/factory.stignore`:

```
# Syncthing exclusions for factory vault
nats/jetstream
blobstore
blobstore.tok
```

Decision record: [ADR-093](architecture/adr/093-operator-audit-three-channel.mdx).
