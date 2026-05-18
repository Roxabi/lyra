# src/lyra/nats/ — NATS In-Tree Integration Layer

## Purpose

Owns the in-process NATS plumbing that connects hub, adapters, and LLM workers.
This is NOT the transport SDK — that lives in `packages/roxabi-nats/` (serialization,
validation, circuit breaker, type-hint resolver). This layer consumes the SDK and wires
it to lyra's domain types.

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
| `lyra.outbound.{platform}.{bot_id}` | hub → adapter | Response chunk delivery |
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
- `NatsChunkEnvelope` (outer stream wrapper) is intentionally unversioned — only the
  inner payload carries a version.
- `NatsLlmClient` uses `roxabi_contracts` Pydantic models (`LlmRequest`, `LlmResponse`,
  `LlmChunkEvent`) directly — JSON, NOT the `roxabi_nats` serialize helpers.
- Error messages forwarded onto the bus must use `type(exc).__name__` only (never
  `str(exc)`) to prevent NATS connection metadata or payload values leaking to users
  (#1212 sanitization rule).

## STT/TTS NATS clients

`nats_stt_client.py` and `nats_tts_client.py` implement `STTProtocol` and `TtsProtocol` over NATS.
Both use `_stt_result_from_wire` / `_tts_result_from_wire` private mappers to convert wire
responses into domain value objects; keep mapping logic in these functions (¬inline in call sites).
`tts_engine_selector.py` and `tts_text_normalization.py` are helpers extracted from the deleted
`lyra.tts` package and relocated here to stay co-located with their consumer (`nats_tts_client.py`).
`stt_helpers.py` provides Whisper noise tokens (`WHISPER_NOISE_TOKENS`), `is_whisper_noise`, and
`mime_from_suffix` — adapter-specific concerns relocated here from `core/ports/stt.py` (#1224 review).

## NatsLlmClient lives here, not in llm/

`NatsLlmClient` (`nats_llm_client.py`) implements the `LlmProvider` protocol and is
imported by `lyra.llm` as a driver. It lives here because it depends on NATS internals
(`WorkerRegistry`, `NatsCircuitBreaker`) — not on any LLM abstraction. Wiring into the
decorator stack happens in `bootstrap/`, not here.

`NatsLlmClient` carries its own `NatsCircuitBreaker`; callers must NOT wrap it with
`CircuitBreakerDecorator` (reserved for `ClaudeCliDriver`).

## Key invariants

- `NatsBus`: caller owns the NATS connection; bus only manages subscriptions.
  Registrations survive `stop()` — safe to restart without re-registering. Never
  `register()` after `start()`.
- `NatsLlmClient` publishes to the canonical literal subject `SUBJECTS.generate_request`
  (from `roxabi_contracts`); queue-group dispatch is handled by the broker.
- All error paths in `NatsLlmClient` return a populated `LlmResult(worker_error=...)`
  or yield a terminal `ResultLlmEvent(is_error=True)` — never raise to the caller.
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
