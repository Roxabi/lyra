# Lyra — Architecture Reference
_Living reference. Synthesized from the 2026-04-06 48-hour review. Update this file when architectural decisions change._

---

## System Overview

Lyra is a personal AI agent engine with a **hub-and-spoke, asyncio, NATS-backed** architecture.

- **Hub process** — single message router; holds all business logic, LLM dispatch, middleware pipeline, session and memory management.
- **Adapter processes** — one per platform (Telegram, Discord). Thin I/O shims. Publish inbound messages to NATS; subscribe to outbound envelopes from NATS.
- **Voice adapter processes** — STT and TTS as independent NATS microservices.
- **NATS** — the only inter-process communication layer. No shared memory, no direct function calls across process boundaries.

Three-process production topology: `lyra hub` + `lyra adapter telegram` + `lyra adapter discord`, all managed by a single supervisord instance under `lyra.service` (systemd).

Single-process dev topology: `lyra start` — hub + adapters in one process with an embedded NATS server auto-started when `NATS_URL` is unset.

---

## Hexagonal Architecture Map

### Dependency Rule

Dependencies point **inward only**:

```
Domain (core/) ← Application (core/hub/) ← Infrastructure (nats/, adapters/, bootstrap/)
```

Verified: zero imports of `lyra.nats`, `lyra.adapters`, or `lyra.bootstrap` anywhere inside `src/lyra/core/`.

### Three Rings

| Ring | Package | Imports from | Verdict |
|------|---------|-------------|---------|
| Domain | `src/lyra/core/` — entities, value objects, domain exceptions, repository interfaces | Only `core/` internals + stdlib | Clean |
| Infrastructure — transport | `src/lyra/nats/` — NATS bus, codecs, clients | `lyra.core.message`, `lyra.core.render_events`, `lyra.core.trust` | Clean |
| Infrastructure — adapters | `src/lyra/adapters/` | `lyra.core.*`, `lyra.nats._serialize`, `lyra.nats._validate` | Clean |
| Composition root | `src/lyra/bootstrap/` | All layers | Expected |

### Ports (Protocols)

| Port | File:line | What it abstracts |
|------|----------|------------------|
| `ChannelAdapter` | `src/lyra/core/hub/hub_protocol.py:24` | Platform I/O (normalize, send, stream, audio) |
| `LlmProvider` | `src/lyra/llm/base.py:33` | LLM backend (complete, is_alive, stream) |
| `STTProtocol` | `src/lyra/stt/__init__.py:19` | Speech-to-text transcription |
| `TtsProtocol` | `src/lyra/tts/__init__.py:23` | Text-to-speech synthesis |
| `PipelineMiddleware` | `src/lyra/core/hub/middleware.py:77` | Inbound message processing stage |
| `OutboundListener` | `src/lyra/adapters/outbound_listener.py:16` | Adapter-side outbound subscription (cache_inbound, start, stop) |
| `Bus[T]` | `src/lyra/core/bus.py` | Generic async message bus (get, put, start, stop, register) |

**Note:** `MemoryManager` (`src/lyra/core/memory.py:38`) has **no Protocol**. It is a concrete class wrapping `roxabi_vault.AsyncMemoryDB`. This is a known gap — swapping the memory backend requires class replacement rather than protocol substitution.

### Adapters (Concrete Implementations of Ports)

| Adapter | Implements | File |
|---------|-----------|------|
| `TelegramAdapter` | `ChannelAdapter` | `src/lyra/adapters/telegram.py` |
| `DiscordAdapter` | `ChannelAdapter` | `src/lyra/adapters/discord.py` |
| `NatsChannelProxy` | `ChannelAdapter` | `src/lyra/nats/nats_channel_proxy.py` |
| `AnthropicSdkDriver` | `LlmProvider` | `src/lyra/llm/drivers/anthropic_sdk.py` |
| `ClaudeCliDriver` | `LlmProvider` | `src/lyra/llm/drivers/claude_cli.py` |
| `NatsSttClient` | `STTProtocol` | `src/lyra/nats/nats_stt_client.py` |
| `NatsTtsClient` | `TtsProtocol` | `src/lyra/nats/nats_tts_client.py` |
| `STTService` | `STTProtocol` | `src/lyra/stt/__init__.py` (adapter process only) |
| `TTSService` | `TtsProtocol` | `src/lyra/tts/__init__.py` (adapter process only) |
| `NatsOutboundListener` | `OutboundListener` | `src/lyra/adapters/nats_outbound_listener.py` |
| `NatsBus[T]` | `Bus[T]` | `src/lyra/nats/nats_bus.py` |
| `LocalBus[T]` | `Bus[T]` | `src/lyra/core/local_bus.py` |

### Layer Boundary Rules

**Forbidden in `core/`:**
- Any import of `lyra.nats`, `lyra.adapters`, or `lyra.bootstrap`
- Any import of `nats` (the library)
- Raw `dict` used as a domain object in business logic (all wire dicts deserialized to typed frozen dataclasses before entering the domain)
- Generic `Exception` raised in domain code — use domain types or typed stdlib exceptions
- Platform-specific routing (`if platform == "telegram"`) — routing via adapter registry keyed by `(Platform, bot_id)`

**Forbidden in `nats/`:**
- Any import of `lyra.adapters` or `lyra.bootstrap`

**Allowed in `bootstrap/`:**
- All layers (composition root)

---

## Complete NATS Topology

### Subject Naming Convention

```
lyra.<direction>.<subsystem>.<platform>.<bot_id>
```

Directions: `inbound` | `outbound` | `voice` | `system`

### All 6 Subject Trees

| Subject Pattern | Direction | Publisher | Subscriber | Queue Group | Notes |
|----------------|----------|----------|-----------|------------|-------|
| `lyra.inbound.{platform}.{bot_id}` | adapter→hub | Adapter (publish_only NatsBus) | Hub NatsBus[InboundMessage] | `hub-inbound` | Unified text + voice messages |
| `lyra.inbound.audio.{platform}.{bot_id}` | adapter→hub | Adapter (publish_only NatsBus) | InboundAudioLegacyHandler | `hub-inbound` | **DEPRECATED** — audio only; phase-out in #534 Slice 2 |
| `lyra.outbound.{platform}.{bot_id}` | hub→adapter | NatsChannelProxy | NatsOutboundListener | `adapter-outbound-{platform}-{bot_id}` | All envelope types: send, stream_start, chunks, stream_error, attachment |
| `lyra.voice.stt.request` | hub→worker | NatsSttClient | STT adapter | `stt-workers` | Request-reply; 60s timeout |
| `lyra.voice.tts.request` | hub→worker | NatsTtsClient | TTS adapter | `tts-workers` | Request-reply; 30s timeout |
| `lyra.system.ready` | adapter→hub | wait_for_hub() | start_readiness_responder() | (none) | Request-reply probe; 30s timeout, 0.5s interval |

Queue group constants are centralized in `src/lyra/nats/queue_groups.py`:
- `HUB_INBOUND = "hub-inbound"`
- `HUB_INBOUND_AUDIO = "hub-inbound-audio"`
- `adapter_outbound(platform, bot_id) -> "adapter-outbound-{platform}-{bot_id}"`

### ASCII Process Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                         NATS Server                              │
│  lyra.inbound.*          lyra.outbound.*     lyra.voice.*       │
│  lyra.inbound.audio.*    lyra.system.ready                      │
└────────┬──────────────────────┬─────────────────────┬───────────┘
         │                      │                     │
         ▼ (subscribe)          │ (publish)           │ (req-reply)
┌──────────────────────────────────────────────────────────────┐
│                        lyra_hub                               │
│                                                               │
│  NatsBus[InboundMessage]  (queue: hub-inbound)               │
│  InboundAudioLegacyHandler (queue: hub-inbound) [DEPRECATED] │
│  start_readiness_responder (lyra.system.ready)               │
│  ↓                                                           │
│  MiddlewarePipeline (10 stages)                              │
│  ↓                                                           │
│  Hub.run() → agent dispatch → NatsChannelProxy               │
│                               ↓ (publish outbound)          │
│  NatsSttClient ─────────────────────────────────────────────►│
│  NatsTtsClient ─────────────────────────────────────────────►│
└───────────────────────────────────────────────────────────────┘
         │ (publish inbound)         ▲ (subscribe outbound)
         │                          │
┌────────▼──────────────────────────┴──────────────────────────┐
│                  lyra_telegram / lyra_discord                  │
│                                                               │
│  NatsBus[InboundMessage] (publish_only=True)                 │
│  NatsBus[InboundAudio]   (publish_only=True) [DEPRECATED]   │
│  NatsOutboundListener    (queue: adapter-outbound-*-*)       │
│  wait_for_hub()  ─── probes lyra.system.ready at startup     │
└───────────────────────────────────────────────────────────────┘
         │ (subscribe)
         ▼
┌──────────────────────────┐
│  lyra_stt / lyra_tts     │
│  STTService / TTSService  │
│  (queue: stt/tts-workers) │
└──────────────────────────┘
```

---

## Message Flows

### Inbound: Telegram → Hub → LLM → Response

```
Telegram API
  → TelegramAdapter.normalize(raw) → InboundMessage
  → NatsBus[InboundMessage].put() (publish_only, lyra.inbound.telegram.{bot_id})
  → [NATS]
  → Hub NatsBus[InboundMessage] handler
  → MiddlewarePipeline:
      TraceMiddleware
      → ValidatePlatformMiddleware
      → ResolveTrustMiddleware
      → TrustGuardMiddleware
      → RateLimitMiddleware
      → SttMiddleware (if modality == "voice": transcribe via NatsSttClient)
      → ResolveBindingMiddleware
      → CreatePoolMiddleware
      → CommandMiddleware (slash commands)
      → SubmitToPoolMiddleware
  → Pool.run_turn() → agent.process()
  → LlmProvider.complete() / stream()
  → NatsChannelProxy.send() / send_streaming()
  → [NATS lyra.outbound.telegram.{bot_id}]
  → NatsOutboundListener._handle_send() / _handle_stream_start()
  → TelegramAdapter.send() / send_streaming()
  → Telegram API
```

### Outbound Streaming: stream_start → chunks → stream_error

```
Hub: NatsChannelProxy.send_streaming(events_iterator)
  → publish {type: "stream_start", stream_id, outbound} to lyra.outbound.*
  → for each RenderEvent:
      publish {stream_id, seq, event_type, payload, done=False}
  → on exception:
      publish {type: "stream_error", stream_id, reason: "streaming_exception"}
      drain iterator
  → always: _active_streams.discard(stream_id)
  → publish {stream_id, seq, event_type: "stream_end", done: True} (sentinel)

Hub shutdown: publish_stream_errors("hub_shutdown")
  → atomic swap: stream_ids = _active_streams; _active_streams = set()
  → publish {type: "stream_error", stream_id, reason: "hub_shutdown"} per active stream

Adapter: NatsOutboundListener._handle()
  → routes on data.get("type"):
      "stream_start" → _handle_stream_start(): create queue + task
      "stream_error" → _handle_stream_error(): enqueue poison pill OR tombstone + evict
      "send"         → _handle_send(): adapter.send()
      "attachment"   → _handle_attachment(): adapter.render_attachment()
  → chunks (no "type" field): _handle_chunk(): enqueue to stream queue
  → decode_stream_events(): reads queue, yields RenderEvent, breaks on stream_end/error
```

Outbound envelope types (all on `lyra.outbound.{platform}.{bot_id}`):

| type field | Struct | Terminal |
|-----------|--------|---------|
| `"send"` | `{type, stream_id, outbound: OutboundMessage}` | Yes |
| `"stream_start"` | `{type, stream_id, outbound: OutboundMessage}` | No |
| `"stream_error"` | `{type, stream_id, reason}` | Yes |
| `"attachment"` | `{type, stream_id, attachment: OutboundAttachment}` | Yes |
| (none) chunks | `{stream_id, seq, event_type, payload, done}` | Only when done=True |

### Voice: STT + TTS Request-Reply

```
STT flow:
  SttMiddleware.__call__(msg, ctx) — msg.modality == "voice"
  → NatsSttClient.transcribe(audio_path)
  → nc.request("lyra.voice.stt.request", payload, timeout=60.0)
  → STT adapter replies: {request_id, ok, text, language, duration_seconds}
  → msg.text = transcript; msg.audio = None
  → continue pipeline

TTS flow:
  NatsTtsClient.synthesize(text, agent_tts=..., language=..., voice=...)
  → nc.request("lyra.voice.tts.request", payload, timeout=30.0)
  → TTS adapter replies: {request_id, ok, audio_b64, mime_type, duration_ms, waveform_b64}
  → SynthesisResult returned to AudioPipeline
  → adapter.render_audio() or render_audio_stream()

Graceful degradation:
  STT timeout/error → STTUnavailableError → stt_unavailable reply dispatched
  TTS timeout/error → TtsUnavailableError → text-only fallback response
  STT not configured → stt_unsupported reply
  TTS not configured (LYRA_VOICE_RESPONSES=0) → text-only responses
```

---

## Extension Points

### Table

| Extension | Protocol/Interface | File:line | Bootstrap injection | Steps |
|-----------|-------------------|----------|---------------------|-------|
| LLM provider | `LlmProvider` | `src/lyra/llm/base.py:33` | `bootstrap/agent_factory.py:_build_shared_base_providers()` | 7 steps (see below) |
| Channel adapter | `ChannelAdapter` | `src/lyra/core/hub/hub_protocol.py:24` | `bootstrap/bootstrap_wiring.py:wire_*_adapters()` | 7 steps (see below) |
| Memory backend | None (concrete `MemoryManager`) | `src/lyra/core/memory.py:38` | `bootstrap/bootstrap_stores.py:open_stores()` | Duck-type or subclass |
| STT engine | `STTProtocol` | `src/lyra/stt/__init__.py:19` | `bootstrap/voice_overlay.py:init_nats_stt()` | Implement transcribe(); inject in voice_overlay |
| TTS engine | `TtsProtocol` | `src/lyra/tts/__init__.py:23` | `bootstrap/voice_overlay.py:init_nats_tts()` | Implement synthesize(); inject in voice_overlay |
| Pipeline middleware | `PipelineMiddleware` | `src/lyra/core/hub/middleware.py:77` | `src/lyra/core/hub/middleware.py:build_default_pipeline()` | Implement __call__; insert in pipeline list |
| Slash command plugin | `plugin.toml` + `handlers.py` | `src/lyra/commands/echo/` (reference) | Auto-discovered by `CommandLoader` from `commands/` dir | Create directory, no registration call |
| Agent personality | TOML + `AgentRow` | `src/lyra/core/agent_seeder.py`, `src/lyra/core/agent_models.py:26` | `lyra agent init` seeds DB; loaded at hub startup | Write TOML, run init |

### Adding a New LLM Provider

1. Create `src/lyra/llm/drivers/mydriver.py`. Implement `LlmProvider` structurally (no Protocol import needed). Set `capabilities = {"streaming": False}`.
2. `complete()` → return `LlmResult`. Set `retryable=False` for non-transient errors.
3. Implement `stream()` only if the backend supports real-time token delivery (duck-typed — checked via `hasattr`).
4. Register in `bootstrap/agent_factory.py:_build_shared_base_providers()`: `providers["my-backend"] = MyDriver(...)`.
5. Add `"my-backend"` to `_VALID_BACKENDS` in `src/lyra/core/agent_config.py:11`.
6. Add branch in `bootstrap/agent_factory.py:_create_agent()` wiring agents with `backend = "my-backend"` to the correct agent class.
7. Set `backend = "my-backend"` in agent TOML/DB.

Wrap in `RetryDecorator → CircuitBreakerDecorator` from `src/lyra/llm/decorators.py` for production use.

### Adding a New Communication Channel

1. Create `src/lyra/adapters/whatsapp.py`. Implement `ChannelAdapter` structurally — no inheritance. Reference: `src/lyra/adapters/telegram.py`.
2. Verify sender identity at the platform level before constructing `InboundMessage`. Never derive `user_id` or `scope_id` from unverified data.
3. Add config model classes to `src/lyra/config.py` (`WhatsAppBotConfig`, `WhatsAppMultiConfig`).
4. Add `wire_whatsapp_adapters()` in `src/lyra/bootstrap/bootstrap_wiring.py` calling:
   - `hub.register_authenticator(Platform.WHATSAPP, bot_id, auth)`
   - `hub.register_adapter(Platform.WHATSAPP, bot_id, adapter)`
   - `hub.register_binding(Platform.WHATSAPP, bot_id, "*", agent_name, key.to_pool_id())`
   - `hub.register_outbound_dispatcher(Platform.WHATSAPP, bot_id, dispatcher)`
5. Add `WHATSAPP = "whatsapp"` to `Platform` enum in `src/lyra/core/message.py`.
6. Call `wire_whatsapp_adapters()` from `bootstrap/hub_standalone.py` and `bootstrap/unified.py`.
7. Update `PLATFORM_META_ALLOWLIST` in `src/lyra/nats/_sanitize.py` with any new platform-specific metadata keys.

For three-process production: adapter publishes to `lyra.inbound.<platform>.<bot_id>`; hub subscribes via `NatsBus`. Outbound arrives via `lyra.outbound.<platform>.<bot_id>`. See `src/lyra/nats/nats_channel_proxy.py` for the NATS proxy pattern.

### Adding a New STT Engine

**In-process engine:**
1. Create class with `async def transcribe(self, path: Path | str) -> TranscriptionResult`.
2. In `bootstrap/voice_overlay.py:init_nats_stt()`, return your engine instead of `NatsSttClient`.

**NATS microservice (zero Lyra code changes):**
1. Listen on `lyra.voice.stt.request` (queue group: `stt-workers`).
2. Reply with `{"request_id": "...", "ok": true, "text": "...", "language": "en", "duration_seconds": 0.0}`.

### Adding a New TTS Engine

**In-process engine:**
1. Create class with `async def synthesize(self, text, *, agent_tts, language, voice, fallback_language) -> SynthesisResult`. Return OGG/Opus with `mime_type="audio/ogg"`.
2. In `bootstrap/voice_overlay.py:init_nats_tts()`, return your engine.

**NATS microservice (zero Lyra code changes):**
1. Listen on `lyra.voice.tts.request` (queue group: `tts-workers`).
2. Reply with `{"request_id": "...", "ok": true, "audio_b64": "...", "mime_type": "audio/ogg", "duration_ms": N, "waveform_b64": "..."}`.
3. Full request schema fields: `text`, `language`, `voice`, `fallback_language`, `chunked`, `engine`, `accent`, `personality`, `speed`, `emotion`, `exaggeration`, `cfg_weight`, `segment_gap`, `crossfade`, `chunk_size`.

### Adding Pipeline Middleware

1. Create class with `async def __call__(self, msg: InboundMessage, ctx: PipelineContext, next: Next) -> PipelineResult`.
2. Call `await next(msg, ctx)` to pass through; return `_DROP` to short-circuit.
3. Open `src/lyra/core/hub/middleware.py:build_default_pipeline()`.
4. Insert into the `MiddlewarePipeline([...])` list at the correct position.

Default pipeline order (10 stages):
```
TraceMiddleware → ValidatePlatformMiddleware → ResolveTrustMiddleware → TrustGuardMiddleware
→ RateLimitMiddleware → SttMiddleware → ResolveBindingMiddleware → CreatePoolMiddleware
→ CommandMiddleware → SubmitToPoolMiddleware
```

### Adding a Slash Command Plugin

1. Create `src/lyra/commands/myplugin/plugin.toml` and `handlers.py`.
2. `plugin.toml` required fields: `name`, `description`, `version`, `priority`, `enabled`, `timeout`, `[[commands]]` with `name`, `description`, `handler`.
3. Handler signature: `async def cmd_name(msg: InboundMessage, pool: Pool, args: list[str]) -> Response`.
4. Enable per-agent in TOML/DB: `plugins.enabled = ["myplugin"]`.
5. `CommandLoader` auto-discovers from `commands/` — no registration needed.

---

## Wire Format and Schema Versioning

### Envelope Coverage

| Envelope | schema_version field | Constant | check_schema_version on receive |
|---------|---------------------|---------|-------------------------------|
| `InboundMessage` | `int = 1` | `SCHEMA_VERSION_INBOUND_MESSAGE` in `core/message.py:17` | Yes — `NatsBus._make_handler()` |
| `InboundAudio` (legacy) | `int = 1` | `SCHEMA_VERSION_INBOUND_AUDIO` in `core/message.py:18` | Yes — `NatsBus._make_handler()` + `InboundAudioLegacyHandler._handle()` |
| `OutboundMessage` | `int = 1` | `SCHEMA_VERSION_OUTBOUND_MESSAGE` in `core/message.py:19` | **MISSING** — constant defined but never consumed on adapter receive side |
| `TextRenderEvent` | `int = 1` | `SCHEMA_VERSION_TEXT_RENDER_EVENT` in `core/render_events.py:22` | Yes — `NatsRenderEventCodec.decode()` |
| `ToolSummaryRenderEvent` | `int = 1` | `SCHEMA_VERSION_TOOL_SUMMARY_RENDER_EVENT` in `core/render_events.py:23` | Yes — `NatsRenderEventCodec.decode()` |

### Version Check Rules (nats/_version_check.py)

- Missing field → treat as version 1 (legacy compat)
- `bool` value → drop (bool is int subclass in Python; explicitly excluded)
- `int <= 0` → drop
- `int > expected` → drop (forward-compat: receiver refuses future versions)
- `int in [1, expected]` → accept
- Rate-limited log: 1 ERROR per envelope type per 60 seconds

### Chunk Envelope (unversioned — known gap)

The streaming chunk envelope `{stream_id, seq, event_type, payload, done}` in `nats/render_event_codec.py` is **not versioned**. The `schema_version` field guards only the inner payload dict. A rename of outer wrapper fields would produce a silent failure. This gap is **acknowledged in `docs/ARCHITECTURE.md`** under "Schema versioning".

---

## Known Architecture Gaps

| Gap | Severity | Location | Notes |
|-----|---------|---------|-------|
| `MemoryManager` has no `MemoryPort` Protocol | Low | `src/lyra/core/memory.py` | Swapping memory backend requires class replacement; acceptable for single backend |
| `InboundAudio` parallel path not retired (Slice 2 pending) | Low | `nats/compat/inbound_audio_legacy.py`, `bootstrap/adapter_standalone.py` | Two inbound audio paths in parallel until #534 Slice 2 complete |
| `_QUEUE_GROUP = "hub-inbound"` hardcoded string in legacy compat handler | Low | `nats/compat/inbound_audio_legacy.py:35` | Should import `HUB_INBOUND` from `queue_groups.py` to prevent silent divergence on rename |
| `OutboundMessage` not version-checked on adapter receive | Low | `src/lyra/adapters/nats_outbound_listener.py:144, 191` | SCHEMA_VERSION_OUTBOUND_MESSAGE defined but never consumed; silent misinterpret risk on first schema bump |
| `_ENVELOPE_VERSIONS` KeyError deferred to handler time | Low | `src/lyra/nats/nats_bus.py:256` | Unregistered item_type raises KeyError inside NATS callback (swallowed), not at start() |
| `NatsSttClient`/`NatsTtsClient` request-reply envelopes carry no schema_version | Info | `nats/nats_stt_client.py`, `nats/nats_tts_client.py` | Internal infra; co-deployment assumption undocumented |
| Chunk envelope outer wrapper (`stream_id`, `seq`, `event_type`) is unversioned | Info | `nats/render_event_codec.py` | Acknowledged in ARCHITECTURE.md |
| `_terminated_streams` set not cleaned by reaper | Low | `src/lyra/adapters/nats_outbound_listener.py:67` | Tombstoned stream IDs that never started a drain task linger indefinitely |
| `assert hub._msg_manager is not None` in SttMiddleware | Medium | `src/lyra/core/hub/middleware_stt.py:91` | Hard crash if Hub instantiated without MessageManager; should be graceful drop |
| `Platform` enum in domain layer must change to add a platform | Info | `src/lyra/core/message.py:22` | Acceptable for a closed enum; acts as platform registration seam |
| LLM backend selection is if/elif in `_create_agent()` | Low | `src/lyra/bootstrap/agent_factory.py:151-206` | Should be a registry lookup; adding a backend requires editing agent_factory |
| `NatsSttClient` and `NatsTtsClient` have no unit tests | Low | `src/lyra/nats/nats_stt_client.py`, `nats_tts_client.py` | Only covered by integration; timeout and error paths untested in isolation |
