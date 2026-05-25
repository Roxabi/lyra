---
title: Storage & Persistence
description: Current truth for all store, persistence, and event-bus decisions in Lyra — agent store, thread store, blobstore, memory scope, and event bus wiring.
---

# Storage & Persistence — Lyra

> Status: LIVING — current truth for store/persistence/event-bus decisions.
> Last updated: 2026-05-24.
> Source ADRs: 008, 022 (amended), 024, 029, 063, 067 (amended), 068. Absorbed via 059: 048.

## Scope

Covers the five persistence surfaces in Lyra: memory scope (what is actually stored at runtime),
the agent config store (SQLite, write-through cache), the thread store (Discord thread
persistence), the blobstore (content-addressed binary archive), and the event bus wiring
pattern. The hexagonal placement of all stores within `lyra.infrastructure` is canonical in
`architecture-patterns.md` — not repeated here.

## Current state

### Memory scope levels

Phase 1 implements two of the five memory levels. **Level 0 (working)** is the LLM context
window — `Pool.history` passed directly to the model, no Lyra code required. **Level 3
(semantic)** is SQLite + `aiosqlite` with BM25 (`rank-bm25`) and vector similarity
(`sqlite-vec`), hybrid search at query time, mandatory `normalized_url` / `resolved_url`
indexed columns for O(1) URL dedup. Levels 1 (session), 2 (episodic), and 4 (procedural)
are deferred until a concrete, measurable trigger arises for each. Deferred ≠ rejected — the
five-level taxonomy in `ARCHITECTURE.md` is the long-term target. → ADR-008

→ See `ARCHITECTURE.md` (Memory Layer) for the full 5-level breakdown with implementation status, compaction details, and L1 TurnStore details.

### Memory level taxonomy (L0–L4)

| Level | Name | Isolation |
|-------|------|-----------|
| L0 Working | `dict` in memory, scoped by `pool_id` | Pool-scoped |
| L1 Session | Store keyed by `(user_id, session_id)` | User + session scoped |
| L2 Episodic | `~/.lyra/memory/episodic/{user_id}/YYYY-MM-DD/` — user_id in path | User-scoped path |
| L3 Semantic | SQLite, `WHERE user_id = ?` mandatory on all queries | User-scoped query |
| L4 Procedural | Global (skills = agent capabilities, not user data) | Global |

### MemoryEntry schema

```python
class MemoryEntry:
    # --- Identity ---
    id: UUID
    user_id: str            # ← ABSOLUTE partition key, never omitted

    # --- Sessions ---
    session_id_created: str
    session_id_modified: str

    # --- Content ---
    level: MemoryLevel      # L0 → L4
    content: str
    embedding: bytes        # sqlite-vec (L3 only)
    tags: list[str]

    # --- Metadata ---
    created_at: datetime
    updated_at: datetime
    count_usage: int        # incremented on each retrieve
    count_edits: int        # incremented on each write/update
    confidence: float       # reliability score (0.0 → 1.0)
    ttl: datetime | None    # auto-expiry (L1/L2)
    source: str             # "user" | "agent" | "system"
```

### SQL isolation rule (non-negotiable)

Every memory query must include `user_id`. Even for stats, aggregate per user.

```sql
SELECT * FROM memory
WHERE user_id = :user_id        -- absolute isolation
  AND level IN (3, 4)           -- requested scope
  AND (ttl IS NULL OR ttl > datetime('now'))
ORDER BY count_usage DESC, updated_at DESC
LIMIT 20;
```

Security invariant: → See `security-routing.md` (#memory-isolation).

### Counter updates

```python
async def retrieve(self, user_id: str, query: str, level: MemoryLevel) -> list[MemoryEntry]:
    entries = await self._search(user_id, query, level)
    for entry in entries:
        await self._increment_usage(entry.id)  # count_usage + 1
    return entries

async def write(self, user_id: str, content: str, level: MemoryLevel, session_id: str) -> MemoryEntry:
    existing = await self._find_similar(user_id, content)
    if existing:
        existing.content = content
        existing.count_edits += 1
        existing.updated_at = datetime.utcnow()
        existing.session_id_modified = session_id
        await self._save(existing)
        return existing
    return await self._create(user_id, content, level, session_id)
```

### Memory implementation status

`user_id` partitioning is active in `prefs_store.py` (L3 queries use `WHERE user_id = ?`). The full `MemoryEntry` metadata schema (count_usage, count_edits, confidence, ttl, source) is not yet applied uniformly — tracked as an extension to #83.

- [x] `user_id` isolation enforced in `prefs_store.py` queries
- [x] L2 path structure uses `{user_id}/` directories (session_lifecycle.py)
- [ ] Full `MemoryEntry` metadata schema with all fields above
- [ ] `count_usage` + `count_edits` auto-increment
- [ ] TTL auto-purge for L1/L2
- [ ] Per-user stats endpoint (usage, size, last activity)

### Agent store (SQLite)

`AgentStore` lives at `lyra.infrastructure.stores.agent_store` (moved from `lyra.core` during
ADR-059 remediation) and inherits from `SqliteStore`. Three tables: `agents`, `bot_agent_map`,
`agent_runtime_state`. Write ordering is DB-first: `execute` → `commit` → update in-memory
cache, consistent with `AuthStore`. Cache covers `agents` and `bot_map` (sync `get()` path);
`agent_runtime_state` is intentionally uncached — every read issues a DB query, every write
goes direct. The two-tier model is documented in the class docstring. `close()` clears the
cache; a closed store must not be reused. → ADR-024

### DB-first agent config + hot-reload

The SQLite `agents` table is the single runtime source of truth. TOMLs under `src/lyra/agents/`
are seed-only files, consumed exclusively by `lyra agent init` and `lyra agent validate`. At
runtime, `AgentBase._maybe_reload()` compares `AgentRow.updated_at` (ISO-8601 string, O(1)
dict lookup from the `AgentStore` cache) against a locally cached timestamp; on change it calls
`agent_row_to_config()`. No TOML is read post-startup. No background polling timer — reload is
lazy, per-message. `lyra agent edit` changes are visible on the next inbound message with no
daemon restart. Persona files (`.persona.toml`) remain file-based; persona hot-reload requires
a DB `upsert()` to trigger. → ADR-029

### Thread store lifecycle

`ThreadStore` (SQLite-backed Discord thread persistence, `discord.db`) is a shared instance
across all Discord adapter instances. Bootstrap creates and connects the store, threads it
through `wire_discord_adapters`, and closes it in the teardown finally-block — after all
adapters have closed. `close()` is removed from `ThreadStoreProtocol`; the protocol expresses
domain behaviour only. The concrete `ThreadStore` type retains `close()` as a bootstrap-internal
method. Teardown order: adapters close → `dc_thread_store.close()` → remaining infrastructure.
The `wire_discord_adapters` function returns `(adapters, dispatchers, thread_store)`. → ADR-063

### BlobStore (content-addressed)

Binary payloads (Telegram/Discord attachments, STT/TTS audio) are stored behind a `BlobStore`
Protocol with three methods: `put`, `get`, `exists`. The v1 backend is `FsBlobStore`: a
SHA-256-addressed flat-FS tree (`/data/lyra/blobs/<sha[:2]>/<sha>`) plus a SQLite index at
`/data/lyra/blobs/index.sqlite` (two tables: `blobs` keyed by `content_hash`; `blob_refs` for
per-ingestion provenance). The backend runs on `lyra-hub` role (M₁) and is exposed via a
dedicated Quadlet container `lyra-blobstore.container` (FastAPI on TCP `:8449`, image
`ghcr.io/roxabi/lyra` + `lyra blobstore serve` subcommand) — V8 issue #1330. Host paths:
`~/.lyra/blobs/` (data, bind-mount into container) and `~/.roxabi/lyra/env/blobstore.env`
(Quadlet env). Direct-FS access is reserved for co-located writers only (telegram_normalize,
hub middleware); all other consumers (e.g., M₂ image-worker) use `HttpBlobStore` — the store
host is invisible to them. Auth: shared bearer token via Podman secret `lyra_blobstore_token`
(`type=mount`) in Phase 1 → per-identity JWT or `auth.db` lookup + `blob_grants` table in
Phase 2 (see ADR-067 §Auth plane). Dedup: `put()` hashes first; if `blobs.content_hash`
exists, only a new `blob_refs` row is appended. Write durability order: write file → `fsync`
→ INSERT. Backup (Phase 1): Restic → Cloudflare R2 daily, `index.sqlite` snapshotted via
SQLite `.backup` API before FS tarball (atomicity invariant). `audio_b64` / `audio_bytes` in
`roxabi-contracts` are deprecated; removal is a coordinated atomic migration across contracts
→ voiceCLI workers → lyra adapters. Adapters download eagerly at ingress (Telegram URL valid
≥1h; Discord CDN URLs expire ~24h). Raw bytes never traverse NATS. MinIO swap triggers: disk
>70% on blobstore volume, HA requirement, or ML S3 demand. → ADR-067 (amended), ADR-068

#### HTTP service (V8 — #1330)

`lyra-blobstore` is a dedicated Quadlet container (`lyra-blobstore.service`) that exposes
`FsBlobStore` over HTTP on port `8449`. It runs on the `lyra-hub` role (M₁, `roxabituwer`)
only — same host as the data volume.

Six endpoints (N1–N6 per spec):

| Method | Path | Description |
|--------|------|-------------|
| `PUT` | `/blobs` | Upload blob; returns `store_key` + `content_hash` |
| `GET` | `/blobs/{store_key}` | Download blob bytes |
| `HEAD` | `/blobs/{store_key}` | Check existence, return metadata headers |
| `DELETE` | `/blobs/{store_key}` | Remove blob |
| `GET` | `/blobs/{store_key}/exists` | Boolean existence check |
| `GET` | `/health` | Readiness + version |

**Client:** `roxabi_blobs.HttpBlobStore` — implements the `BlobStore` Protocol using `httpx`
async. Zero `lyra.*` imports; importable from voiceCLI, imageCLI, and any other cross-repo
consumer without pulling in the Lyra runtime.

**Auth (Phase 1):** shared bearer token via Podman secret `lyra_blobstore_token`
(`type=mount`, `/run/secrets/lyra_blobstore_token`, uid=1500, gid=1500, mode=0400).
Unauthorized requests return `401` with an audit row (`result: "unauthorized"`).

**Wiring:** same-host clients (Telegram, Discord, clipool adapters) use Quadlet DNS
`http://lyra-blobstore:8449`. Cross-host clients use Tailnet MagicDNS (see `## Cross-host
access pattern` below). The Protocol call-site is identical in both cases.

#### Cross-host access pattern

M₂ workers (`llm-worker`, `image-worker`) and any other Tailnet member construct
`HttpBlobStore` pointing to:

```
http://roxabituwer.goose-logarithm.ts.net:8449
```

Transport encryption is provided by Tailnet (WireGuard) — V8 is HTTP at the application
layer. A future phase may add mTLS at the app layer if the Tailnet boundary is broadened
(tracked as TBD in ADR-067 §Auth plane Phase 2).

**Topology rule:** only the `lyra-blobstore` process uses `FsBlobStore` directly. Every
other process — hub, adapters, M₂ workers — constructs `HttpBlobStore`. Direct-FS access
is a violation of this boundary from V8 onwards.

**Backup and restore:** see `docs/QUADLET-DEPLOYMENT.md` (§ Rotating the BlobStore bearer
token and §§ Backing up the BlobStore / Restore invariant) for the operator runbook.

### Event bus DI

`PipelineEventBus` is injected via `Hub.__init__(event_bus: "PipelineEventBus | None" = None)`.
Bootstrap wiring is in `bootstrap/factory/wiring_helpers.py:262`. No module-level singleton
(`get_event_bus` / `set_event_bus`) exists in the codebase. The original singleton approach
(ADR-022 Option A) was accepted as a short-term measure and subsequently replaced by DI per
ADR-025 F-10. Every emit site used an optional guard (`if bus := get_event_bus()`) — this
guard pattern is gone; the bus is either injected or absent. → ADR-022 (amended)

## Key invariants

- **DB-first writes:** all `AgentStore` writes commit to SQLite before updating the in-memory
  cache. Cache is never ahead of DB on the success path.
- **DB is runtime authority:** `agents` table is the single runtime source of truth for agent
  config. TOMLs are read only during `lyra agent init` / `lyra agent validate`.
- **Blob content-addressed:** every binary payload is stored by SHA-256 hash; identical content
  written N times occupies one file.
- **Blob write order:** file → `fsync` → INSERT into `blob_refs` / `blobs`. Reversed order
  risks dangling index rows on crash.
- **BlobStore interface boundary:** raw bytes never cross NATS; only `BlobRef` envelopes do.
- **Bootstrap owns store lifecycles:** `connect()` / `close()` on all stores are bootstrap
  concerns. Domain protocols (`ThreadStoreProtocol`) carry no lifecycle methods.
- **No module-level singletons for event bus:** `PipelineEventBus` is injected; emitters
  do not reach for process-global state.
- **Lazy hot-reload, no background timer:** per-message `updated_at` comparison is the
  sole reload trigger. No asyncio task spawned.
- **Memory levels 1, 2, 4 deferred, not rejected:** trigger conditions for each are documented
  in ADR-008; adding level 1 is a one-line `Pool` change.
- **Blob v1 loss model is explicit:** single-host, no replication; loss is tolerated. This
  assumption is load-bearing and must be revisited if data becomes non-re-collectable.

## Open questions / known gaps

- `SimpleAgent` does not register `/add`, `/explain`, `/summarize` session commands (only
  `AnthropicAgent` does). The gap pre-dates ADR-029 and surfaces on every
  `_rebuild_command_router()` call. Fix: `SimpleAgent._register_session_commands()` override.
- Persona hot-reload (changing `.persona.toml` without a DB `upsert()`) no longer fires
  automatically post ADR-029. Operators must run `lyra agent edit` or `lyra agent init --force`.
- The TOML fallback path in `multibot.py` (degraded mode for users who have not run
  `lyra agent init`) emits a startup warning but remains in the codebase. It is a known
  transitional artifact.
- `_wire_adapters` return tuple is now 5 elements; revisit if it reaches 6+.
- BlobStore backup atomicity: naive `tar` of FS + DB while live produces an inconsistent
  backup. SQLite `.backup` API or WAL checkpoint before FS snapshot is mandatory if a
  backup cron is added. Not yet implemented.
- Blob mount inode/disk alerts (threshold 80%) are specified in ADR-067 but not yet wired.
- v1 BlobStore is single-host but ecosystem-transparent: cross-host consumers use `HttpBlobStore`
  (V8 HTTP service); MinIO swap is triggered only by disk pressure, HA need, or S3 demand. → ADR-068
- TurnStore + L3 memory (including L1 sessions — `pool_sessions` table in `turns.db`) use direct-write to SQLite (co-located, ADR-068 pattern α deviation). Tolerated until either (a) the TurnStore α-refactor issue (#1331) lands, OR (b) a 3rd adapter is added on top of TurnStore — whichever comes first (ADR-073 three-strikes rule).
- JetStream KV `lyra-state` is used exclusively for hub readiness signaling (`hub.ready` key, `roxabi_nats/readiness.py`). It is NOT a session store.

## See also

- Hex layer canonical (`lyra.infrastructure`) → `architecture-patterns.md` (absorbs ADR-048)
- Vault as memory backend → `~/projects/roxabi-vault/`
- JetStream volume isolation → ADR-067 Neutral + issue #1055

## ADR archive

| ADR | Title | Status |
|-----|-------|--------|
| 008 | Phase-1 memory scope | Accepted |
| 022 | EventBus DI migration | Amended |
| 024 | AgentStore SQLite | Accepted |
| 029 | DB-first agent config | Accepted |
| 063 | ThreadStore teardown | Accepted |
| 067 | BlobStore content-addressed | Accepted (amended 2026-05-24) |
| 068 | Ecosystem Service Plane | Accepted |
| 048 | Lyra infrastructure layer | Absorbed by ADR-059 |
