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
| `nats/jetstream/` | JetStream persistent storage | **Excluded** (host-local, large WAL files) |
| `blobstore/` | BlobStore shard tree + SQLite index (real directory) | **Excluded** (large binary data — see Syncthing exclusions below) |

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
| `~/.roxabi/factory/blobstore/` | Large append-only binary data; high churn — excluded from Syncthing |

Exclusion rule (add to `.stignore` in `~/.roxabi/factory/`):

```
# Syncthing exclusions for Lyra
nats/jetstream
blobstore
```
