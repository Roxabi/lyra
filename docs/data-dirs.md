# Data Directories

Lyra stores runtime data in two root locations:

| Host path | Purpose | Container path |
|---|---|---|
| `~/.lyra/` | Application vault (databases, secrets, config, env) | `/home/lyra/.lyra` (via `lyra-data.volume`) |
| `/data/lyra/blobs/` | BlobStore shard tree + SQLite index | `/home/lyra/.lyra/blobstore` (via bind-mount in `lyra-blobstore.container`) |

---

## `~/.lyra/` — Application vault

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
| `blobstore/` | **Symlink / container view** — points to `/data/lyra/blobs/` | **Excluded** (canonical path is `/data/lyra/blobs/`) |

**Note:** `~/.lyra/blobstore/` is a bind-mount view from the container perspective. The canonical host directory is `/data/lyra/blobs/`. Backups and Syncthing exclusions must reference the canonical path.

---

## `/data/lyra/blobs/` — BlobStore canonical host path

| Subdirectory / File | Purpose |
|---|---|
| `index.sqlite` | SQLite manifest of all stored blobs |
| `<sha[:2]>/<sha>` | Content-addressed prefix-shard tree (immutable, hardlink-safe) — see SSoT below |

This directory is **excluded from Syncthing** across all hosts. It is large, append-only binary data and should be backed up via the BlobStore snapshot procedure (`docs/QUADLET-DEPLOYMENT.md §Backing up the BlobStore`).

> **SSoT for layout:** `packages/roxabi-blobs/src/roxabi_blobs/fs_store.py` defines the exact prefix-shard algorithm (`_shard_for(content_hash) → content_hash[:2]`) and write order (`file → fsync(file) → fsync(shard dir) → INSERT blobs → INSERT blob_refs`).

---

## `~/.roxabi-vault/` — Vault CLI database

Mounted into `lyra-hub` so the `vault put` subprocess can write to it. Not part of Syncthing.

---

## Syncthing exclusions

Syncthing syncs `~/.lyra/` across M₁, M₂, and laptop. The following paths are excluded:

| Path | Reason |
|---|---|
| `~/.lyra/nats/jetstream/` | Host-local JetStream WAL; large, not portable |
| `~/.lyra/blobstore/` | Redirects to `/data/lyra/blobs/`; large binary data |
| `/data/lyra/blobs/` | Canonical BlobStore host path; excluded from Syncthing |

Exclusion rule (add to `.stignore` in `~/.lyra/`):

```
# Syncthing exclusions for Lyra
nats/jetstream
blobstore
```

The `/data/lyra/blobs/` path is outside `~/.lyra/` so it does not need an `.stignore` rule — it is excluded by simply not adding it to Syncthing at all.
