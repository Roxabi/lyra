# Architecture Review — 2026-04-06

## Overall Verdict

**PASS** — Architecture is sound and consistently enforced. The hexagonal boundaries are clean, no infra leaks into the domain, and all six major changes reviewed strengthen the design rather than weaken it. Two targeted concerns are flagged below.

---

## Hexagonal Architecture Compliance

### Dependency rule — evidence

| Layer | What it imports | Verdict |
|-------|----------------|---------|
| `core/` (domain + application) | Only `core/` internals + stdlib | Clean |
| `nats/` (infrastructure) | `lyra.core.message`, `lyra.core.render_events`, `lyra.core.trust` | Clean — infra imports domain types |
| `adapters/` (infrastructure) | `lyra.core.*`, `lyra.nats._serialize`, `lyra.nats._validate` | Clean |
| `bootstrap/` (wiring) | All layers | Expected — this is the composition root |

Verified via grep: zero imports of `lyra.nats`, `lyra.adapters`, or `lyra.bootstrap` anywhere inside `src/lyra/core/`. The dependency arrow is strictly inward.

### STT protocol boundary

`Hub.__init__` accepts `stt: STTProtocol | None`. `STTProtocol` is defined in `lyra.stt` (a thin wrapper package) as a pure `Protocol` with one method: `transcribe(path) -> TranscriptionResult`. Both the in-process `STTService` and the NATS-backed `NatsSttClient` satisfy this protocol structurally. The hub never imports NATS; `NatsSttClient` is injected by `bootstrap/voice_overlay.py`. **Boundary is correctly placed.**

### `ChannelAdapter` as port

`ChannelAdapter` is a `Protocol` in `core/hub/hub_protocol.py`. It imports nothing from `adapters/` or `nats/`. Concrete adapters (`TelegramAdapter`, `DiscordAdapter`, `NatsChannelProxy`) implement it structurally in the infrastructure layer. The hub calls methods on the protocol type only — it never holds a reference to a concrete class. **Port/adapter pattern is correctly applied.**

### No anti-patterns found in the domain

- No raw `dict` used as domain object in business logic — all wire `dict` values are deserialized into typed frozen dataclasses before entering the domain.
- No `if platform == "telegram"` routing inside `Hub` — routing uses the adapter registry keyed by `(Platform, bot_id)`.
- No generic `Exception` raised in domain code — errors use domain types or typed stdlib exceptions.
- `Hub` is under control: orchestration only, no LLM calls or platform formatting.

---

## Modularity / Pluggability

### LLM

**Pluggable. No hub coupling to a specific backend.**

- `LlmProvider` Protocol in `lyra.llm.base` defines `complete()`, `is_alive()`, and the duck-typed optional `stream()`.
- Backends (`AnthropicSdkDriver`, `ClaudeCliDriver`) registered in a `ProviderRegistry` by name string.
- Decorator stack (`CircuitBreaker → SmartRouting → Retry → Driver`) assembled in `bootstrap/`, not in `llm/` — correct composition-root placement.
- Agents call `hub._stt` and `hub._tts` through `STTProtocol` / `TtsProtocol` — both swappable at bootstrap.

To add a new LLM backend: implement `LlmProvider`, register the backend name in `ProviderRegistry`. Zero changes to hub or domain code.

### Communication channels

**Pluggable. Adding a new platform (e.g. WhatsApp) requires:**

1. Create `adapters/whatsapp.py` implementing `ChannelAdapter` — no new superclass needed.
2. Add `Platform.WHATSAPP` to the `Platform` enum in `core/message.py`.
3. Register the adapter in `bootstrap/`.

No changes to `Hub`, `Pool`, `MessagePipeline`, or any existing adapter. The `_sanitize.py` `PLATFORM_META_ALLOWLIST` would need new keys — this is a deliberate allow-list, not a hardcoded platform branch.

One concern: the `Platform` enum in `core/message.py` is in the domain layer and currently has hardcoded `TELEGRAM = "telegram"` and `DISCORD = "discord"`. Adding a platform requires a domain-layer change. This is acceptable for an enum (it is a closed value set by design), but teams should be aware that the enum acts as the seam for platform registration.

### Memory

**Pluggable via dependency injection.**

- `MemoryManager` wraps `AsyncMemoryDB` from the `roxabi-vault` external package.
- Hub holds `_memory: MemoryManager | None` and sets it via `hub.set_memory(manager)`.
- No `MemoryManager` import in any adapter or NATS module.
- Memory is injected at bootstrap (`open_stores()` → `hub.set_memory()`).

There is no formal `MemoryPort` Protocol (unlike `LlmProvider` and `STTProtocol`). The hub and agents call `MemoryManager` directly by class. This is a mild abstraction gap — swapping the memory backend requires replacing the class rather than swapping an implementation of a protocol. This is currently acceptable (single memory backend, tight lifetime) but is worth an ADR if a second backend is ever considered.

---

## InboundMessage Unification (Slice 1, #534)

**Assessment: well-designed. Extensible. Compat shim is time-bounded.**

### What changed

- `AudioPayload` value object added to `core/audio_payload.py` — pure dataclass, no imports from infra.
- `InboundMessage` gains `modality: Literal["text", "voice"] | None` and `audio: AudioPayload | None`.
- `SttMiddleware` added to the middleware pipeline — transcribes when `modality == "voice"`, strips `audio` field after success.
- Dedicated `InboundAudioBus` and the `AudioPipeline.run()` consumer loop are replaced by the unified path.
- `nats/compat/inbound_audio_legacy.py` bridges legacy `lyra.inbound.audio.*` subjects → unified `InboundMessage`.

### Correctness

- The compat shim is clearly annotated as Phase 1 only with a `# Delete in Phase 2` comment. The class, its test suite, and the `nats/compat/` package all carry the deletion instruction. Migration path is unambiguous.
- `InboundAudio` is retained in `core/message.py` as a still-used type (adapters still publish it on the legacy subject, and the `ChannelAdapter.normalize_audio()` protocol method still returns it). Its removal is gated on Phase 2 adapter migration.
- The STT pipeline stage correctly inserts itself after `RateLimitMiddleware` and before `ResolveBindingMiddleware` — rate limiting gates the expensive transcription.
- Slash-command injection guard on transcribed text (`if transcript.startswith("/")`) is present.
- `_STT_STAGE_OUTCOMES` counters use module-level mutable dicts — acceptable for Slice 1 with a `# TODO(#534-slice2)` annotation.

### Concern: `InboundAudio` parallel path not fully retired

The adapter standalone bootstrap (`bootstrap/adapter_standalone.py`) still creates a second `NatsBus[InboundAudio]` per bot alongside the unified `NatsBus[InboundMessage]`:

```python
inbound_audio_bus: Bus[InboundAudio] = NatsBus(
    nc=nc, bot_id=bot_id, item_type=InboundAudio,
    subject_prefix="lyra.inbound.audio", publish_only=True,
)
```

This is the correct Slice 1 behaviour — adapters still publish on the legacy subject, the hub bridges it. But it means there are **two inbound paths for audio in parallel** until Phase 2 is complete. The legacy handler's `_QUEUE_GROUP = "hub-inbound"` ties the load-balancing to a hardcoded constant rather than importing from `queue_groups.py`. This is a minor consistency gap and a potential silent divergence risk if `HUB_INBOUND` is ever renamed.

**Recommended:** import `HUB_INBOUND` from `queue_groups.py` in `inbound_audio_legacy.py` instead of the inline string `"hub-inbound"`.

---

## Voice NATS Decoupling (#e0bd438)

**Assessment: clean separation achieved. Hub is now voice-infrastructure-agnostic.**

### Before

STT and TTS ran in-process in the hub. `Hub` directly called `voicecli.transcribe` (blocking in a thread). TTS lived in `audio_pipeline.py` alongside the hub.

### After

- `NatsSttClient` and `NatsTtsClient` in `lyra.nats/` implement `STTProtocol` / `TtsProtocol` structurally.
- Both use NATS request-reply (`nc.request`). The hub is unaware of NATS at this level — it just calls `hub._stt.transcribe(path)`.
- `bootstrap/voice_overlay.py` contains the factory logic (`init_nats_stt`, `init_nats_tts`). Decision to use in-process vs NATS STT/TTS is made at bootstrap, not at call time.
- `audio_pipeline.py` is now a slim TTS dispatch helper with a module docstring explicitly noting it is temporary ("Slice 2 will relocate this helper").

### Residual coupling

- `SttMiddleware` accesses `hub._msg_manager` and `hub._stt` via direct attribute access on the `Hub` instance it receives through `PipelineContext`. This is not a leakage — `SttMiddleware` lives in `core/hub/` and `Hub` is the expected scope. However, `assert hub._msg_manager is not None` is a hard assertion in production middleware. If `msg_manager` is not configured (test or partial bootstrap), this panics. Consider a graceful fallback or a startup validation check.

---

## Wire Format / Schema Versioning (#d970d3a)

**Assessment: correct design. Two minor gaps documented in ARCHITECTURE.md itself.**

### Strengths

- Every inbound envelope (`InboundMessage`, `InboundAudio`) and outbound envelope (`OutboundMessage`, `TextRenderEvent`, `ToolSummaryRenderEvent`) carries `schema_version: int = 1`.
- Constants (`SCHEMA_VERSION_INBOUND_MESSAGE`, etc.) centralised in `core/message.py` and `core/render_events.py` — single source of truth for bump procedures.
- `check_schema_version` in `nats/_version_check.py`: missing → treats as version 1 (backward compat). Non-int / out-of-range / future version → drop with ERROR log. Rate-limited to 1 log per 60s per envelope to prevent log-flood DoS.
- Per-instance `_version_mismatch_drops` counters in both `NatsBus` and `NatsOutboundListener` — observable without a metrics backend.

### Gap 1: outer render-event chunk envelope is unversioned

The chunk envelope `{stream_id, seq, event_type, payload, done}` in `render_event_codec.py` is an implicit, unversioned contract. The `schema_version` field guards only the inner payload dict. A rename of the outer wrapper's fields would produce a silent failure (the outer envelope would deserialize with `None` values, then the inner check would pass or fail on the payload). This gap is **already acknowledged in `docs/ARCHITECTURE.md`** under "Schema versioning" — no new finding.

### Gap 2: schema_version not on `NatsTtsClient` / `NatsSttClient` request-reply payloads

The NATS request-reply envelopes for voice services (`lyra.voice.stt.request`, `lyra.voice.tts.request`) do not carry `schema_version`. These subjects are internal infrastructure, not hub↔adapter contracts, so they fall outside the scope of the envelope versioning scheme. This is acceptable as long as both the client and the service adapter are always co-deployed. Document this assumption explicitly if the voice services become separately versioned.

---

## Outbound Listener Protocol (#aa57879)

**Assessment: clean extraction. Structural protocol correctly applied.**

`OutboundListener` Protocol in `adapters/outbound_listener.py` defines only three methods: `cache_inbound`, `start`, `stop`. It uses `TYPE_CHECKING`-guarded imports for `InboundMessage | InboundAudio` — no runtime import of domain types in the protocol file.

Adapters (`TelegramAdapter`, `DiscordAdapter`) hold `_outbound_listener: OutboundListener | None` typed at the protocol level. `NatsOutboundListener` satisfies it structurally without inheritance.

This is correct hexagonal usage: the adapter layer defines the shape it needs from its collaborator; the concrete implementation (`NatsOutboundListener`) lives in infrastructure and satisfies the shape. Tests can inject a mock that implements only the three methods.

One observation: `outbound_listener.py` imports `InboundMessage | InboundAudio` inside `TYPE_CHECKING`. At runtime, adapters call `listener.cache_inbound(hub_msg)` where `hub_msg` is already a domain type. No runtime type check is needed — this is the correct pattern.

---

## Risks & Concerns

Ranked by severity:

| # | Risk | Severity | Location |
|---|------|----------|---------|
| 1 | `_QUEUE_GROUP = "hub-inbound"` hardcoded string in `inbound_audio_legacy.py` — silent divergence if `HUB_INBOUND` constant is renamed | Low | `nats/compat/inbound_audio_legacy.py:35` |
| 2 | `assert hub._msg_manager is not None` in `SttMiddleware.__call__` — hard crash in test/partial bootstrap if msg_manager not configured | Low | `core/hub/middleware_stt.py:91` |
| 3 | `MemoryManager` has no `MemoryPort` Protocol — memory backend is not formally swappable without a class replacement | Low | `core/memory.py`, hub.py |
| 4 | `Platform` enum in domain layer requires change to add a new platform — acceptable for a closed enum but acts as a registration seam | Informational | `core/message.py:22` |
| 5 | Outer render-event chunk envelope (`stream_id`, `seq`, `event_type`) is unversioned — schema changes are silent | Informational | `nats/render_event_codec.py` (acknowledged in ARCHITECTURE.md) |
| 6 | `NatsSttClient` / `NatsTtsClient` request-reply envelopes carry no `schema_version` — co-deployment assumption undocumented | Informational | `nats/nats_stt_client.py`, `nats/nats_tts_client.py` |

---

## Recommended Actions

| Priority | Action | Owner layer |
|----------|--------|-------------|
| 1 | In `nats/compat/inbound_audio_legacy.py`, replace `_QUEUE_GROUP = "hub-inbound"` with `from lyra.nats.queue_groups import HUB_INBOUND` to prevent silent divergence on rename. | `nats/compat/` |
| 2 | Replace `assert hub._msg_manager is not None` in `SttMiddleware` with a graceful early-return (`_DROP` + log error) or add a startup validation that requires `msg_manager` when STT is configured. | `core/hub/middleware_stt.py` |
| 3 | Track Phase 2 of #534 explicitly: once all adapters publish on the unified `lyra.inbound.*` subject, delete `nats/compat/`, the `inbound_audio_bus` in `adapter_standalone.py`, and `ChannelAdapter.normalize_audio()` from the protocol. The shim must not become permanent. | `nats/compat/`, `bootstrap/adapter_standalone.py` |
| 4 | If a second memory backend is ever introduced, extract a `MemoryPort` Protocol (mirroring `STTProtocol`) so the hub depends on the protocol, not the concrete class. Not urgent for single-backend operation. | `core/memory.py` |
| 5 | Document the co-deployment assumption for voice NATS request-reply envelopes (`lyra.voice.stt.request`, `lyra.voice.tts.request`) in `docs/ARCHITECTURE.md` under "Schema versioning". | docs |
