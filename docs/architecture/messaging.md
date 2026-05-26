---
title: Messaging & NATS — Lyra
description: Living current-truth document for all messaging and NATS transport decisions in Lyra.
---

# Messaging & NATS — Lyra

> Status: LIVING — current truth for messaging/NATS decisions.
> Last updated: 2026-05-26.
> Source ADRs: 001, 002, 035, 036, 065, 076. Absorbed via 045: 037, 040, 047, 062.

## Scope

This document covers the three NATS planes (messages, persistence, typing/lifecycle),
routing key semantics, hub dispatch invariants, NATS subject naming, the Hub→Adapter
streaming chunk protocol, and the hub readiness probe. It does not cover security/ACL
policy (see `security-routing.md`), cross-project contract schemas (see `contracts.md`),
or LLM streaming internals (see `llm-streaming.md`).

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

`RoutingContext` is the outbound companion to `RoutingKey` — it carries the per-response routing struct needed for the adapter to deliver a response to exactly the right bot, chat, and thread. Defined in `src/lyra/core/message.py`.

```python
class RoutingContext:
    channel: str            # "telegram" | "discord" | "cli"
    bot_id: str             # identifier of the bot that must reply
    chat_id: str            # Telegram chat_id / Discord guild+channel
    thread_id: str | None   # forum thread, Discord thread
    reply_to_message_id: str | None  # native Telegram/Discord threading
    user_id: str
    session_id: str
```

**Populated at intake (in `normalize()`):**

```python
def normalize(self, update: TelegramUpdate) -> Message:
    return Message(
        ...
        routing=RoutingContext(
            channel="telegram",
            bot_id=self.bot_id,
            chat_id=str(update.message.chat.id),
            thread_id=str(update.message.message_thread_id) if update.message.is_topic_message else None,
            reply_to_message_id=str(update.message.message_id),
            user_id=str(update.message.from_user.id),
            session_id=self.make_session_id(update),
        )
    )
```

**Verified at outbound (in the Adapter):**

```python
async def send(self, response: Response) -> None:
    ctx = response.routing
    assert ctx.channel == self.channel, f"Wrong channel: {ctx.channel}"
    assert ctx.bot_id == self.bot_id,   f"Wrong bot: {ctx.bot_id}"
    await self.bot.send_message(
        chat_id=ctx.chat_id,
        text=response.content,
        message_thread_id=ctx.thread_id,
        reply_to_message_id=ctx.reply_to_message_id,
    )
```

Security invariant (outbound verification) → `security-routing.md` (#routing).

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
| Messages | `lyra.{inbound,outbound}.<platform>.<bot_id>` | Core | ephemeral | adapters ↔ hub | hub, adapters | bidirectional hub↔adapter routing of user content |
| Persistence | `lyra.turns.>` | JetStream durable (stream `LYRA_TURNS`, `MaxAge=24h`, WorkQueue) | durable | hub | turn-writer | append-only state changes requiring at-least-once delivery |
| Typing / Lifecycle | `lyra.typing.<platform>.<bot_id>` | Core | ephemeral | hub (future: workers) | adapters | ephemeral display-feedback events (typing indicators; future progress UX) — lossy-OK because consumer state auto-expires |

**Choosing a plane when adding a subject:**

1. Decide durability first — hard guarantee needed → JetStream → Persistence plane; lossy-OK
   → Core → Messages or Typing depending on direction.
2. If durability + direction shape matches an existing plane, use that plane's prefix; do not
   open a new one.
3. A new plane requires a separate ADR — and a justification that durability + direction +
   lifecycle ownership all differ from the three existing planes.

**Distinguish from sibling subjects** — these are NOT typing-plane members despite the
surface resemblance:

- `lyra.progress.<job_id>` (#1044, future) — job-internal progress, keyed on `job_id`, not on
  `WorkScope`.
- `lyra.clipool.heartbeat` and the `*.heartbeat` family — internal liveness, control-plane.
- `$KV.lyra-state.hub.ready` — persistent flag in JetStream KV, watched by adapters.

→ ADR-076

### NATS subject naming

All subjects follow `lyra.{domain}.{qualifier...}` (domain-first, NATS convention
`{app}.{noun}.{verb...}`). Live subjects:

| Subject | Direction | Purpose |
|---|---|---|
| `lyra.inbound.{platform}.{bot_id}` | adapter → hub | User message delivery |
| `lyra.outbound.{platform}.{bot_id}` | hub → adapter | Response chunk delivery |
| `lyra.typing.{platform}.{bot_id}` | hub → adapter | Ephemeral typing indicator lifecycle (Typing plane — Epic #1375, lands with T1 #1376) |
| `lyra.llm.request` | hub → worker | LLM compute offload |
| `lyra.llm.health.{worker_id}` | worker → hub | Satellite LLM worker heartbeats |
| `lyra.clipool.cmd` | hub → CliPool | Submit turn + resume UUID |
| `lyra.clipool.heartbeat` | CliPool → hub | CliPool subprocess runner health announcements |
| `lyra.clipool.control` | hub → CliPool | Control commands (reset, drain) |
| `lyra.voice.tts.heartbeat` | voice-tts → hub | TTS worker liveness signal for hub availability checks |
| `lyra.voice.stt.heartbeat` | voice-stt → hub | STT worker liveness signal for hub availability checks |
| `lyra.llm.heartbeat` | llm-worker → hub | LLM worker liveness signal for hub availability checks |
| `lyra.image.heartbeat` | image-worker → hub | Image worker liveness signal for hub availability checks |
| `lyra.system.ready` | adapters + workers → hub | Startup ready announcement; hub tracks liveness on subscribe |

System-plane subjects (JetStream API + KV bucket) are governed by per-identity grants in
`deploy/nats/acl-matrix.json` rather than restated here; see ADR-045 / ADR-046 + #1293.

| Subject | Direction | Purpose |
|---|---|---|
| `$JS.API.>` | hub + adapters + workers → server | JetStream API surface for KV reads and consumer create (currently wildcard; tighter scoping in #1293) |
| `$KV.lyra-state.>` | hub → server (write); adapters + workers ← server (read) | Direct KV bucket access — hub publishes `hub.ready`, others watch via `wait_for_hub` |

`{platform}` is lowercase ASCII (`telegram`, `discord`). `{bot_id}` is a numeric string
matching `^[1-9][0-9]*$` — a leading-zero or non-numeric value produces a shadow subject
that bypasses per-bot ACL rules; this is a startup error. `scope_id` is intentionally
absent from subjects; the hub resolves it from the envelope body. Control-plane subjects
(`lyra.hub.command.*`, `lyra.monitor.*`) are reserved but not yet implemented.

→ ADR-035

### Streaming chunk protocol

`RenderEvent` iterators cross the NATS boundary as discrete `NatsChunkEnvelope` messages
on the persistent outbound subject (not ephemeral reply inboxes). The envelope carries:
`stream_id` (UUIDv4, generated by the Adapter for request-response turns; Hub-generated
for proactive messages), `seq` (0-indexed, monotonically increasing), `event_type`
(`"text"` | `"tool_summary"` | `"error"`), `payload` (discriminated by type), and
`done` (true on last message including error termination). In V1, exactly one text chunk
is emitted per turn (`is_final=true, done=true`); adapters must not assume multiple text
chunks. A gap in `seq` or a missing `done=true` after `stream_timeout_s` (default 30s)
is treated as `STREAM_ABORTED`.

→ ADR-036

### NATS render-event codec

The `NatsRenderEventCodec` (source: `src/lyra/nats/render_event_codec.py`) encodes and decodes
`RenderEvent` instances to/from the wire chunk format. Both `NatsChannelProxy` (hub, encodes) and
`NatsOutboundListener` (adapter, decodes) import from this single class.

**Registry shape:**

```python
_registry: dict[type, CodecBranch]     # keyed by RenderEvent subtype class
_by_type_str: dict[str, CodecBranch]   # inverse index for O(1) decode lookup
_SYNTHETIC_TERMINALS: frozenset[str] = frozenset({"stream_end", "stream_error"})
```

Both maps are built once in `__init__` and are immutable thereafter. `decode()` checks
`_SYNTHETIC_TERMINALS` before the registry lookup so clean stream close never emits an
"unknown event_type" warning.

**Registered event families (v2 only):**

| Family | Event types |
|---|---|
| Text triplet | `text_start`, `text_delta`, `text_end` |
| Text chunk (compat) | `text_chunk` |
| Run lifecycle | `run_started`, `run_finished` (terminal), `run_error` (terminal) |
| ToolCall lifecycle | `tool_call_start`, `tool_call_args`, `tool_call_end`, `tool_call_result` |
| Reasoning lifecycle | `reasoning_start`, `reasoning_delta`, `reasoning_end` |

The v1 events `TextRenderEvent` (`event_type="text"`) and `ToolSummaryRenderEvent`
(`event_type="tool_summary"`) have been removed from `src/lyra/core/messaging/render_events.py`
and from the registry (issue #1192, Slice 3). All consumer paths and dual-emit sites have been
migrated to v2. Adding a new `RenderEvent` subtype requires a single registry insertion;
`TestRegistryCompleteness` fails loudly at CI if the registry is missing a union member.

→ ADR-072

### Schema versioning

Every hub↔adapter envelope (`InboundMessage`, `InboundAudio`, `OutboundMessage`) carries a `schema_version: int` field guarded by a `SCHEMA_VERSION_*` module-level constant in `src/lyra/core/message.py` and `src/lyra/core/render_events.py`. The outer `NatsChunkEnvelope` (`{stream_id, seq, event_type, payload, done}`) is intentionally unversioned — only the inner payload is guarded.

**Schema version bump procedure (4 steps):**

1. Bump the `SCHEMA_VERSION_<ENVELOPE>` constant in `src/lyra/core/message.py` or `src/lyra/core/render_events.py` by 1.
2. Update the `schema_version` field default on the corresponding envelope to match.
3. Coordinate a simultaneous deploy of `lyra_hub` + `lyra_telegram` + `lyra_discord`. Rolling deploys across a version bump produce loud ERROR logs on still-old receivers.
4. Verify: `grep SCHEMA_VERSION_ src/lyra/core/*.py`.

→ See `ARCHITECTURE.md` (Schema versioning section) for full detail on the receiver drop-and-log policy and the unversioned outer envelope note.

### Hub readiness probe

On startup the hub writes `hub.ready = b"true"` to the `lyra-state` JetStream KV bucket
via `announce_hub_ready(nc)`. Adapters probe via `wait_for_hub(nc)`: immediate
`kv.get("hub.ready")`, falling back to `kv.watch("hub.ready")` if the key is absent.
The key persists across adapter restarts — adapters starting after the hub see the key
immediately. If JetStream is unavailable, the probe degrades gracefully (WARNING log,
adapter starts anyway). The hub is the sole creator of the `lyra-state` bucket; adapters
that encounter `BucketNotFoundError` log a WARNING and return False rather than racing
to provision. The legacy `start_readiness_responder` on `lyra.system.ready` remains for
identities with `allow_responses: true` (health endpoints, CLI tools) but is no longer
part of the adapter startup path.

→ ADR-065

## Key invariants

- Use `RoutingKey.to_pool_id()` to derive pool IDs; inline f-string construction in hub code is prohibited.
- `scope_id` encodes conversation scope, not user identity; rate limiting keys on `(platform.value, bot_id, user_id)`.
- Hub event-loop task must never be killed by a missing adapter; all lookup failures are caught by the middleware layer.
- Pool creation must always be guarded by `PoolManager._lock`; any refactor must preserve the lock guard.
- NATS subject tokens must never contain dots; `bot_id` must match `^[1-9][0-9]*$` — validated at startup.
- `scope_id` is resolved from the inbound envelope body, never encoded in the NATS subject.
- `stream_id` for request-response turns is generated by the Adapter and copied by the Hub into all outbound chunks.
- Any gap in `seq` or timeout waiting for the next chunk is treated as fatal `STREAM_ABORTED` — no reorder buffer.
- Hub is the sole creator of the `lyra-state` KV bucket; adapters must not provision it.
- Control-plane subjects (`lyra.hub.command.*`) must remain deny-listed until a dedicated ADR approves their payloads.

## Open questions / known gaps

- `NatsInboundEnvelope` full schema (including `platform_context`, `scope_id`, remaining `TurnInput` fields) is deferred to Slice C implementation; only the `stream_id` correlation contract is specified.
- Multi-adapter deployments (multiple adapter instances per bot) are not yet supported; the current model assumes one adapter per bot. `stream_id` in the subject token is identified as the future path if needed.
- JetStream-only environments lose readiness probe coverage; adapters start unconditionally — operators must monitor for WARNING logs.
- Incremental text chunks (`is_final=false`) are reserved for V2 and not produced by the current `StreamProcessor`.
- Control-plane subjects (`lyra.hub.command.*`, `lyra.monitor.health`) are reserved but no ADR has approved their payloads or publisher identity.

## See also

- Security & ACLs → `/home/mickael/projects/lyra/docs/architecture/security-routing.md` (covers ADR-051, 064, 046, 057, 069)
- Cross-project contracts → `/home/mickael/projects/lyra/docs/architecture/contracts.md` (covers ADR-045, 049, 052)
- LLM streaming pipeline → `/home/mickael/projects/lyra/docs/architecture/llm-streaming.md` (covers ADR-032, 070)

## ADR archive

| ADR | Title | Status |
|-----|-------|--------|
| 001 | RoutingKey | Accepted — amended by #125 (scope_id) |
| 002 | Hub dispatch contracts | Accepted — updated 2026-05-08 (middleware decomp, lock-based pool, channel removed) |
| 035 | NATS subject naming | Accepted |
| 036 | RenderEvent chunk protocol | Accepted |
| 065 | KV readiness probe | Accepted |
| 072 | Codec registry pattern (v2 RenderEvent) | Accepted — supersedes ADR-032 v1 wire shape |
| 076 | Three NATS planes (messages / persistence / typing) | Accepted — 2026-05-26; operational landing with Epic #1375 |
| 037, 040, 047, 062 | (various transport ADRs) | Absorbed by ADR-045 (roxabi-nats SDK) |
