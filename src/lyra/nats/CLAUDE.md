# src/lyra/nats/ — NATS In-Tree Integration Layer

## Purpose

Owns the in-process NATS plumbing that connects hub, adapters, and domain worker clients.
This is NOT the transport SDK — that lives in `packages/roxabi-nats/` (serialization,
validation, circuit breaker, type-hint resolver). This layer consumes the SDK and wires
it to lyra's domain types.

## Domain clients here vs transport in `lyra.transport`

`nats_{tts,stt,image}_client.py` are domain clients that compose:
- `WorkerPoolClient` (from `lyra.transport`) for routing + CB + heartbeat tracking
- A domain codec (`nats_{tts,stt,image}_codec.py` — encode/decode of wire bytes ↔ domain values)

Domain clients here own:
- Their `roxabi_contracts.voice|image` subject helpers (`per_worker_tts/stt`)
- Backward-compat `start(nc) / stop()` methods that delegate to `pool.start(nc) / pool.stop()`

The transport SDK lives in `lyra.transport` (NOT here). Subject routing logic stays here
because subjects are per-domain. See `src/lyra/transport/CLAUDE.md` if it exists.

LLM is the outlier: `lyra.llm.llm_client.LlmClient` (canonical pilot) lives in `lyra.llm`
because its codec depends on `lyra.core.messaging.events` (LlmEvent union) and that ownership
is in the llm package.

## Boundary — what belongs here vs. packages

| Concern | Lives in |
|---------|----------|
| Serialize/deserialize, token validation | `packages/roxabi-nats/` (SDK) |
| Contract schemas (LlmRequest, LlmResponse, subjects) | `packages/roxabi-contracts/` |
| Bus subscriptions, publish, staging queue | `lyra.nats` (here) |
| Hub↔Adapter chunk encoding/decoding | `lyra.nats` (here — `render_event_codec.py`) |
| LLM worker heartbeat tracking, score-based routing | `lyra.nats` (here) |

## Import rules

- `lyra.nats` may import from `lyra.core` and both `packages/`.
- `lyra.core`, `lyra.llm`, `lyra.adapters`, `lyra.commands` may import from `lyra.nats`.
- `lyra.nats` must NOT import from `lyra.adapters`, `lyra.llm`, or `lyra.commands`
  (would create cycles — all driver wiring belongs in `bootstrap/`).

## Subject naming scheme

Pattern: `lyra.{domain}.{qualifier...}`

| Subject | Direction | Purpose |
|---------|-----------|---------|
| `lyra.inbound.{platform}.{bot_id}` | adapter → hub | User message delivery |
| `lyra.outbound.{platform}.{bot_id}` | hub → adapter | Text response chunk delivery (Core, at-most-once — unchanged) |
| `lyra.outbound.audio.{platform}.{bot_id}` | hub → adapter | Audio delivery (JetStream `LYRA_OUTBOUND_AUDIO` `MaxAge=24h`, durable pull consumer `outbound-audio-{platform}-{bot_id}`, at-least-once + KV dedup `lyra_outbound_audio_sent` — ADR-077) |
| `lyra.llm.generate.request` | hub → worker | LLM compute (queue-group dispatched) |
| `lyra.llm.health.{worker_id}` | worker → hub | LLM worker heartbeats |
| `lyra.clipool.cmd` | hub → clipool | LLM subprocess requests |
| `lyra.clipool.heartbeat` | clipool → hub | CliPool health |
| `lyra.clipool.control` | hub → clipool | Reset / drain |

`{platform}` = lowercase ASCII (`telegram`, `discord`).
`{bot_id}` = numeric string matching `^[1-9][0-9]*$` — validated at startup; a
leading-zero or non-numeric value produces a shadow subject that bypasses per-bot ACL.
`{scope_id}` is intentionally absent from subjects — resolved from the envelope body.
Per-worker score-routed subjects (`lyra.llm.generate.request.{worker_id}`) were removed
in #1104 to match the canonical ACL allow list; do NOT reintroduce them.

## Envelope and encoding contract

- `NatsBus` publishes via `roxabi_nats.serialize()` and deserializes via
  `roxabi_nats.deserialize_dict()` with the `TYPE_REGISTRY_RESOLVER`.
- Every hub↔adapter envelope (`InboundMessage`) carries `schema_version: int` guarded by
  `SCHEMA_VERSION_*` constants in `lyra.core.messaging.message`. Drop + log on mismatch;
  version bump requires simultaneous hub + adapter deploy.
- The outer chunk envelope (a plain dict with `stream_id`, `seq`, `event_type`, `payload`, `done`)
  is intentionally unversioned — only the inner payload carries a version.
- LLM wire encoding is handled by `LlmCodec` in `lyra.llm` — it uses `roxabi_contracts`
  Pydantic models (`LlmRequest`, `LlmResponse`, `LlmChunkEvent`) directly — JSON, NOT
  the `roxabi_nats` serialize helpers.
- Error messages forwarded onto the bus must use `type(exc).__name__` only (never
  `str(exc)`) to prevent NATS connection metadata or payload values leaking to users
  (#1212 sanitization rule).

## STT/TTS/Image NATS clients

`nats_stt_client.py`, `nats_tts_client.py`, and `nats_image_client.py` implement their
respective domain protocols over NATS. Since #1278 each client is a thin domain wrapper
that composes `WorkerPoolClient` (from `lyra.transport`) with a codec:

- `nats_tts_codec.py` / `nats_stt_codec.py` / `nats_image_codec.py` — pure encode/decode,
  no I/O; convert wire bytes ↔ domain value objects.
- Domain clients call `pool.request_with_routing(subject_fn, payload)` or
  `pool.stream_request(payload)` — they never call `nc.new_inbox()` or `nc.subscribe()`
  directly.

`tts_engine_selector.py` and `tts_text_normalization.py` are helpers co-located with their
consumer (`nats_tts_client.py`). `stt_helpers.py` provides Whisper noise tokens
(`WHISPER_NOISE_TOKENS`), `is_whisper_noise`, and `mime_from_suffix`.

## Key invariants

- `NatsBus`: caller owns the NATS connection; bus only manages subscriptions.
  Registrations survive `stop()` — safe to restart without re-registering. Never
  `register()` after `start()`.
- `WorkerPoolClient` (from `lyra.transport`) owns CB + heartbeat subscription; accepts
  `WorkerRegistry` via DI (bootstrap/factory owns the instance). Domain clients here DO
  NOT touch `nc.new_inbox()` / `nc.subscribe()`
  directly — they call `pool.request_with_routing(subject_fn, payload)` or
  `pool.stream_request(payload)`.
- All error paths in domain clients return either a populated domain value object
  (`TranscriptionResult`/`SynthesisResult`/`ImageResult` with `.error` field) or raise the
  domain exception (`TtsUnavailableError`, etc.) — never NATS-specific exceptions.
- `NatsRenderEventCodec` is the single source of truth for hub↔adapter chunk encoding;
  both `NatsChannelProxy` (hub side) and `NatsOutboundListener` (adapter side) import it.
  Adding a new `RenderEvent` subtype requires a registry insertion here.

## ADR references

- ADR-035 — NATS subject naming
- ADR-036 — RenderEvent chunk protocol
- ADR-045 — NATS transport SDK (roxabi-nats extraction)
- ADR-049 — Contract schemas (roxabi-contracts)
- ADR-065 — KV readiness probe
- ADR-072 — Codec registry pattern (v2 RenderEvent)

→ Full messaging/NATS decisions: `docs/architecture/messaging.md`
→ Contract schemas: `docs/architecture/contracts.md`
→ Security/ACL: `docs/architecture/security-routing.md`
