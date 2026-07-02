---
title: Messaging & NATS — factory
description: Living current-truth document for messaging and NATS routing decisions in factory.
---

# Messaging & NATS — factory

> Status: LIVING — current truth for messaging/NATS decisions.
> Last updated: 2026-07-02.
> Source ADRs: 001, 065, 076 (absorbs 035), 084 → `job-model.md`. Archived into this page: 002, 095. Wire protocol → ADR-100 (`llm-streaming.md`). Absorbed via 045: 037, 040, 047, 062.

## Scope

This document covers the three NATS planes (messages, persistence, typing/lifecycle),
routing key semantics, hub dispatch invariants, NATS subject naming, schema versioning
of hub↔adapter envelopes, and the hub readiness probe. It does not cover security/ACL
policy (see `security-routing.md`), the transport layer and cross-project contract
schemas (see `contracts.md`), or the streaming chunk protocol, render-event codec, and
LLM streaming internals (see `llm-streaming.md`).

## Current state

### Routing key

`RoutingKey(platform, bot_id, scope_id)` is a 3-field `NamedTuple` that uniquely identifies
a conversation scope within a bot on a platform. The third field was renamed from `user_id`
to `scope_id` (amendment #125): it encodes conversation context (`chat:555`, `thread:111`,
`channel:222`) derived from `Message.extract_scope_id()`, not user identity. Two separate
chats from the same user produce two independent `RoutingKey`s and two independent pools.
User identity for rate limiting and authentication stays in `Message.user_id`, keyed on
`(platform.value, bot_id, user_id)` — not on `scope_id` — so users cannot bypass rate
limits by switching chats.

→ ADR-001

### RoutingContext

`RoutingContext` is the outbound companion to `RoutingKey` — carries `channel`, `bot_id`, `chat_id`, `thread_id`, `reply_to_message_id`, `user_id`, `session_id`. Defined in `src/factory/core/messaging/message.py`. Populated in `normalize()` at intake; adapter asserts `channel` + `bot_id` match before delivery (security invariant → `security-routing.md` #routing).

→ ADR-002 (#152)

### Hub dispatch

Adapter lookup errors never kill the hub event-loop; error handling is absorbed by the
middleware pipeline in `hub/middleware/` and `hub/pipeline/` (the original `Hub.run()`
monolith has been decomposed — see ADR-059). Pool creation is guarded by
`PoolManager._lock` (a `threading.Lock`) wrapping an explicit existence check plus LRU
eviction via `OrderedDict.move_to_end()` — strictly stronger than the original
`setdefault()` mandate for TOCTOU safety. Pool IDs are always derived via
`RoutingKey.to_pool_id() -> str`, producing `f"{platform.value}:{bot_id}:{scope_id}"`.
Inline f-string construction of pool IDs in hub code is prohibited. `Message.channel` was
removed entirely during the Phase 1b→2 refactor; the canonical routing fields are now
`scope_id`, `channel_id`, and `channel_type`.

→ ADR-002

### NATS planes (catalogue)

Every NATS subject in Lyra belongs to exactly one of three planes. The plane decides the
NATS type (Core vs JetStream), the durability contract, and the keying shape:

| Plane | Subject prefix | NATS type | Durability | Producers | Consumers | When to use |
|---|---|---|---|---|---|---|
| Messages | `factory.{inbound,outbound}.<platform>.<bot_id>` | Core | ephemeral | adapters ↔ hub | hub, adapters | bidirectional hub↔adapter routing of user content |
| Persistence | `factory.turns.>` | JetStream durable (stream `FACTORY_TURNS`, `MaxAge=24h`, WorkQueue) | durable | hub, telegram-adapter, discord-adapter | turn-writer | append-only state changes requiring at-least-once delivery |
| Typing / Lifecycle | `factory.typing.<platform>.<bot_id>` | Core | ephemeral | hub (future: workers) | adapters | ephemeral display-feedback events (typing indicators; future progress UX) — lossy-OK because consumer state auto-expires |
| Audio delivery | `factory.outbound.audio.<platform>.<bot_id>` | JetStream durable (stream `FACTORY_OUTBOUND_AUDIO`, `MaxAge=24h`, Limits retention) | durable | hub | audio-consumer per bot (`outbound-audio-{platform}-{bot_id}`) | durable outbound audio chunks — exactly-once delivery to bot audio sender; dedup via KV `factory_outbound_audio_sent` (TTL=900s) |
| Job dispatch | `factory.jobs.<domain>.<verb>` | JetStream WorkQueue (stream `FACTORY_JOBS`, WorkQueue retention) | durable | hub (provisioner), workers (publishers) | job workers per domain | durable job dispatch with at-most-once delivery; DLQ routing on `MAX_DELIVERIES` advisory |

> Note: clipool-worker is intentionally excluded from publishing `factory.turns.write`. It is a downstream command worker, not a user-message source — the upstream adapter records the turn before the dispatch reaches clipool. See ADR-075 and acl-matrix.json (`clipool-worker.notes`) for the full rationale.

**Choosing a plane when adding a subject:**

1. Decide durability first — hard guarantee needed → JetStream → Persistence plane; lossy-OK
   → Core → Messages or Typing depending on direction.
2. If durability + direction shape matches an existing plane, use that plane's prefix; do not
   open a new one.
3. A new plane requires a separate ADR — and a justification that durability + direction +
   lifecycle ownership all differ from the three existing planes.

**Distinguish from sibling subjects** — these are NOT typing-plane members despite the
surface resemblance:

- `factory.job.<job_id>.progress` (#1044, #1793) — job-internal progress, keyed on `job_id`, not on
  `WorkScope`.
- `factory.clipool.heartbeat` and the `*.heartbeat` family — internal liveness, control-plane.
- `$KV.factory-state.hub.ready` — persistent flag in JetStream KV, watched by adapters.
- `$KV.factory-state.roster.<platform>` — platform bot roster index (`roster.telegram`, `roster.discord`), read by adapters after `wait_for_hub`.

→ ADR-076

### NATS subject naming

All subjects follow `factory.{domain}.{qualifier...}` (domain-first, NATS convention
`{app}.{noun}.{verb...}`). Live subjects:

| Subject | Direction | Purpose |
|---|---|---|
| `factory.inbound.{platform}.{bot_id}` | adapter → hub | User message delivery |
| `factory.outbound.{platform}.{bot_id}` | hub → adapter | Response chunk delivery |
| `factory.outbound.audio.<platform>.<bot_id>` | hub → audio-consumer | Durable outbound audio chunks (JetStream, stream `FACTORY_OUTBOUND_AUDIO`); filter is exact 5-token subject; consumer durable = `outbound-audio-{platform}-{bot_id}` |
| `factory.typing.{platform}.{bot_id}` | hub → adapter | Ephemeral typing indicator lifecycle (Typing plane — Epic #1375, lands with T1 #1376) |
| `factory.llm.generate.request` | hub → worker | LLM compute offload |
| `factory.llm.health.{worker_id}` | worker → hub | Satellite LLM worker heartbeats |
| `factory.jobs.claude` | hub → CliPool | Submit turn + resume UUID |
| `factory.clipool.heartbeat` | CliPool → hub | CliPool subprocess runner health announcements |
| `factory.clipool.control` | hub → CliPool | Control commands (reset, drain) |
| `factory.voice.tts.heartbeat` | voice-tts → hub | TTS worker liveness signal for hub availability checks |
| `factory.voice.stt.heartbeat` | voice-stt → hub | STT worker liveness signal for hub availability checks |
| `factory.voice.tts.lifecycle.{list,status}` | hub → voice-tts | TTS catalogue + runtime status (ADR-095) |
| `factory.voice.stt.lifecycle.{list,status}` | hub → voice-stt | STT catalogue + runtime status (ADR-095) |
| `factory.dashboard.voice.capabilities` | web-adapter → hub | Dashboard BFF: aggregated voice catalogue |
| `factory.llm.heartbeat` | llm-worker → hub | LLM worker liveness signal for hub availability checks |
| `factory.image.heartbeat` | image-worker → hub | Image worker liveness signal for hub availability checks |
| `factory.system.ready` | adapters + workers → hub | Startup ready announcement; hub tracks liveness on subscribe |
| `factory.jobs.<domain>.<verb>` | hub → worker | JetStream WorkQueue job dispatch (stream `FACTORY_JOBS`); at-most-once per consumer; DLQ lane: `factory.jobs.dlq.<domain>` |
| `factory.jobs.dlq.<domain>` | DlqRouter → job workers | Exhausted jobs re-published by hub `DlqRouter` on `MAX_DELIVERIES` advisory (advisory → `MSG.GET` → republish with `Roxabi-Dlq-*` headers → `MSG.DELETE`) |
| `factory.job.<job_id>.steer` | hub → omp-worker | Hub-initiated mid-flight steering prompt for active omp job (`_rpc_bridge.py` subscribe side; ACL: `hub pub`, `omp-worker sub`; #1812) |
| `factory.job.<job_id>.progress` | omp-worker → consumers | Per-job progress events published by `_rpc_bridge.py` on message/tool updates (ACL: `omp-worker pub`) |
| `factory.job.<job_id>.result` | omp-worker → consumers | Terminal job result published by `_rpc_bridge.py` on agent completion (ACL: `omp-worker pub`) |

> Pre-#1793, `results` and `progress` were top-level sibling subjects; unified into `factory.job.<id>.*` by #1793/#1849.

System-plane subjects (JetStream API + KV bucket) are governed by per-identity grants in
`deploy/nats/acl-matrix.json` rather than restated here; see ADR-045 / ADR-046 + #1293.

| Subject | Direction | Purpose |
|---|---|---|
| `$JS.API.>` | hub + adapters + workers → server | JetStream API surface for KV reads and consumer create (currently wildcard; tighter scoping in #1293) |
| `$KV.factory-state.>` | hub → server (write); adapters + workers ← server (read) | Direct KV bucket access — hub publishes `hub.ready` + `roster.<platform>`; adapters probe readiness via `wait_for_hub`, then read roster keys |
| `$KV.factory-active-jobs.>` | hub → server (write); router + dashboard ← server (read) | Active-jobs registry (#1796) — hub is sole writer (open/refresh/close); bucket-level TTL liveness (120s), `allow_direct=False` so readers ride `STREAM.MSG.GET` (#1572) |

`{platform}` is lowercase ASCII (`telegram`, `discord`). `{bot_id}` is a numeric string
matching `^[1-9][0-9]*$` — a leading-zero or non-numeric value produces a shadow subject
that bypasses per-bot ACL rules; this is a startup error. `scope_id` is intentionally
absent from subjects; the hub resolves it from the envelope body. Control-plane subjects
(`factory.hub.command.*`, `factory.monitor.*`) are reserved but not yet implemented.

→ ADR-035

### Voice lifecycle & capabilities

Voice workers expose two NATS surfaces per modality (ADR-095, mirroring the LLM split):
health (`factory.voice.tts.heartbeat` / `factory.voice.stt.heartbeat` — liveness only; a
heartbeat MAY carry a `catalog_revision` hash, MUST NOT carry the full catalogue) and
lifecycle (`factory.voice.tts.lifecycle.{list,status}` / STT equivalent — catalogue +
runtime status via `VoiceLifecycleRequest` / `VoiceLifecycleResponse` on `ContractEnvelope`).
The dashboard BFF asks the hub on `factory.dashboard.voice.capabilities`; the hub fans out
lifecycle `list` through `VoiceLifecycleClient`. Ownership split: blobstore owns clone-sample
bytes; voiceCLI owns the engine/sample catalogue and engine-capabilities SSoT (voiceCLI-internal,
ratified by ADR-095 — not restated here); the hub owns only per-agent voice TTS prefs
(`AgentTTSConfig` — `engine` + `sample_id`, persisted as `voice_json`). VRAM discipline
ratified by ADR-095: TTS keeps at most one cached engine (LRU, surfaced to the dashboard as
`max_cached_engines`), STT keeps one warm model, and no TTS engine is pre-warmed at boot.
Clone engines resolve `TtsRequest.sample_id` via local cache → blobstore GET → engine clone.

→ ADR-095

### FACTORY_JOBS DLQ flow

When a `FACTORY_JOBS` consumer exhausts its `MaxDeliver` attempts, the NATS server <!-- drift-ignore -->
emits a `$JS.EVENT.ADVISORY.CONSUMER.MAX_DELIVERIES.FACTORY_JOBS.>` advisory. <!-- drift-ignore -->

The hub's `DlqRouter` (`infrastructure/jobs/dlq_router.py`) handles each advisory:

1. Parse advisory JSON → extract `stream_seq` and `deliveries`.
2. `jsm.get_msg("FACTORY_JOBS", seq=stream_seq)` — fetch the original message body + subject.
3. Derive `factory.jobs.dlq.<domain>` from the original subject (`_extract_domain`).
4. Publish to `factory.jobs.dlq.<domain>` with `Roxabi-Dlq-Orig-Subject`, `Roxabi-Dlq-Deliveries`, and `Roxabi-Dlq-Stream-Seq` headers.
5. `jsm.delete_msg("FACTORY_JOBS", stream_seq)` — remove from stream to prevent replay.

`factory.jobs.dlq.>` is an in-stream subject enumerated in `FACTORY_JOBS` SUBJECTS (not a <!-- drift-ignore -->
separate stream). `DlqRouter.start()` is wired in `_bootstrap_hub_standalone` before
`announce_hub_ready` (ordering guard enforced by `test_dlq_router_started_before_announce_hub_ready`).

→ ADR-088

### Streaming chunk protocol

→ Moved to `llm-streaming.md` § Streaming chunk protocol (2026-07-02) — chunk envelope, sentinels, ordering contract.

### NATS render-event codec

→ Moved to `llm-streaming.md` § NATS render-event codec (2026-07-02) — codec registry, v2 event families, sentinel handling.

### Schema versioning

Every hub↔adapter envelope (`InboundMessage`, `AudioPayload`, `OutboundMessage`) carries a `schema_version: int` field guarded by a `SCHEMA_VERSION_*` module-level constant in `src/factory/core/messaging/message.py` and `src/factory/core/messaging/render_events.py`. The outer NatsChunkEnvelope (`{stream_id, seq, event_type, payload, done}`) is intentionally unversioned — only the inner payload is guarded.

**Schema version bump procedure (4 steps):**

1. Bump the `SCHEMA_VERSION_<ENVELOPE>` constant in `src/factory/core/messaging/message.py` or `src/factory/core/messaging/render_events.py` by 1.
2. Update the `schema_version` field default on the corresponding envelope to match.
3. Coordinate a simultaneous deploy of `factory_hub` + `factory_telegram` + `factory_discord`. Rolling deploys across a version bump produce loud ERROR logs on still-old receivers.
4. Verify: `grep SCHEMA_VERSION_ src/factory/core/*.py`.

→ See `ARCHITECTURE.md` (Schema versioning section) for full detail on the receiver drop-and-log policy and the unversioned outer envelope note.

### Hub readiness probe

On startup the hub writes `hub.ready = b"true"` to the `factory-state` JetStream KV bucket
via `announce_hub_ready(nc)`. Adapters probe via `wait_for_hub(nc)`: immediate
`kv.get("hub.ready")`, falling back to `kv.watch("hub.ready")` if the key is absent.
The key persists across adapter restarts — adapters starting after the hub see the key
immediately. If JetStream is unavailable, the probe degrades gracefully (WARNING log,
adapter starts anyway). The hub is the sole creator of the `factory-state` bucket; adapters
that encounter `BucketNotFoundError` log a WARNING and return False rather than racing
to provision. The legacy `start_readiness_responder` on `factory.system.ready` remains for
identities with `allow_responses: true` (health endpoints, CLI tools) but is no longer
part of the adapter startup path.

→ ADR-065

### Bot roster KV (`factory-state`)

Standalone Telegram and Discord adapters load their platform bot list from JetStream KV
after `wait_for_hub` — they do not open `config.db`. BotStore on the hub host remains the
write SSoT; KV holds an adapter-facing projection only.

**Keys** (platform index, one `kv.get` per adapter process):

| Key | Writer | Reader | Purpose |
|---|---|---|---|
| `roster.telegram` | hub, `factory bot init` (dual-write) | `factory-telegram` | `TelegramMultiConfig` seed |
| `roster.discord` | hub, `factory bot init` (dual-write) | `factory-discord` | `DiscordMultiConfig` seed |

**Publish ordering (hub boot):** `publish_watch_channels` → `publish_bot_roster` →
`announce_hub_ready`. Adapters must not start roster load before `wait_for_hub` succeeds.

**Payload** (`PlatformRosterDocument`, `roxabi_contracts.state.bot_roster`):

```json
{
  "schema_version": 1,
  "updated_at": "2026-06-19T12:00:00Z",
  "bots": [
    {
      "bot_id": "lyra",
      "agent": "lyra_default",
      "webhook_enabled": false
    }
  ]
}
```

Discord entries may include `auto_thread` and `thread_hot_hours`. Auth fields (`owner_users`,
`trusted_*`, `default_trust`) are **forbidden** on the wire — stripped at publish and rejected
at parse (`extra="forbid"`).

**Read path:** `seed_bot_roster(js, platform)` in `bootstrap/wiring/kv_bot_roster.py` via
`$JS.API.STREAM.MSG.GET` (same transport as `seed_watch_channels`). Unlike `watch_channels`
(degrades to empty), roster failures are **fatal** — missing key, malformed JSON, or empty
`bots[]` exits the adapter with an operator-facing message.

**ACL:** unchanged — existing `$KV.factory-state.>` subscribe grants cover roster keys (#1946).

## Transport layer

→ Moved to `contracts.md` § Transport layer (2026-07-02) — three-layer composition, typed `Result` boundary, `HttpTransport` skeleton.

## Key invariants

- Use `RoutingKey.to_pool_id()` to derive pool IDs; inline f-string construction in hub code is prohibited.
- `scope_id` encodes conversation scope, not user identity; rate limiting keys on `(platform.value, bot_id, user_id)`.
- Hub event-loop task must never be killed by a missing adapter; all lookup failures are caught by the middleware layer.
- Pool creation must always be guarded by `PoolManager._lock`; any refactor must preserve the lock guard.
- NATS subject tokens must never contain dots; `bot_id` must match `^[1-9][0-9]*$` — validated at startup.
- `scope_id` is resolved from the inbound envelope body, never encoded in the NATS subject.
- `stream_id` for request-response turns is generated by the Adapter and copied by the Hub into all outbound chunks.
- Any gap in `seq` or timeout waiting for the next chunk is treated as fatal `STREAM_ABORTED` — no reorder buffer.
- Hub is the sole creator of the `factory-state` KV bucket; adapters must not provision it.
- Hub publishes `roster.<platform>` before `hub.ready`; adapters read roster only after `wait_for_hub`.
- Roster KV documents must never contain auth fields (`owner_users`, `trusted_*`, `default_trust`).
- Control-plane subjects (`factory.hub.command.*`) must remain deny-listed until a dedicated ADR approves their payloads.
- Any future durable media type (video, document) must use a distinct 5-token subject family (`factory.outbound.<media>.<platform>.<bot_id>`) and a distinct durable consumer — never share the audio consumer, never collapse onto the 4-token text path. Reuse the `FACTORY_OUTBOUND_AUDIO` stream only if subjects and retention needs align (ADR-077, upheld by ADR-079).

## Open questions / known gaps

- NatsInboundEnvelope full schema (including `platform_context`, `scope_id`, remaining TurnInput fields) is deferred to Slice C implementation; only the `stream_id` correlation contract is specified.
- Multi-adapter deployments (multiple adapter instances per bot) are not yet supported; the current model assumes one adapter per bot. `stream_id` in the subject token is identified as the future path if needed.
- JetStream-only environments lose readiness probe coverage; adapters start unconditionally — operators must monitor for WARNING logs.
- Incremental text chunks (`is_final=false`) are reserved for V2 and not produced by the current `StreamProcessor`.
- Control-plane subjects (`factory.hub.command.*`, `factory.monitor.health`) are reserved but no ADR has approved their payloads or publisher identity.

## See also

- Security & ACLs → `security-routing.md` (covers ADR-046, 051, 057, 064, 069)
- Cross-project contracts & transport layer → `contracts.md` (covers ADR-045, 049, 052, 095)
- LLM streaming pipeline, chunk protocol & render-event codec → `llm-streaming.md` (covers ADR-028, 032, 036, 070, 072)
- Job plane & active-jobs registry → `job-model.md`

## ADR archive

| ADR | Title | Status |
|-----|-------|--------|
| 001 | RoutingKey | Accepted — amended by #125 (scope_id) |
| 002 | Hub dispatch contracts | Superseded — archived (invariants live in § Hub dispatch above) |
| 035 | NATS subject naming | Superseded by ADR-076 — archived (grammar absorbed there) |
| 036 | RenderEvent chunk protocol | Superseded by ADR-100 — archived (see `llm-streaming.md`) |
| 065 | KV readiness probe | Accepted |
| 072 | Codec registry pattern (v2 RenderEvent) | Accepted — supersedes ADR-032 v1 wire shape |
| 076 | Three NATS planes (messages / persistence / typing) | Accepted — 2026-05-26; operational landing with Epic #1375 |
| 077 | Outbound audio subject family | Superseded by ADR-079 |
| 079 | Audio NATS contract — axial consolidation | Accepted — 2026-05-30 |
| 095 | Voice lifecycle plane — heartbeat vs capabilities listing | Superseded — archived (invariants live in § Voice lifecycle above) |
| 037, 040, 047, 062 | (various transport ADRs) | Absorbed by ADR-045 (roxabi-nats SDK) |
