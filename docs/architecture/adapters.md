---
title: Adapters (Inbound Channels)
description: Current truth for Telegram, Discord, CLI, and audio adapter decisions in Lyra.
---

# Adapters (Inbound Channels) — Lyra

> Status: LIVING — current truth for Telegram/Discord/CLI/audio adapter decisions.
> Last updated: 2026-05-09.
> Source ADRs: 003, 013, 014, 015, 020, 023, 039.

## Scope

Inbound channel adapters are the processes that receive platform events (Telegram webhooks,
Discord gateway messages, CLI input, voice audio) and deliver them to the hub over NATS.
This page covers Telegram webhook dispatch, Discord adapter, CLI entry points, audio routing
in/out, and TTS preference overlay. It excludes the CliPool worker harness (see
`workers-tooling.md`) and routing key conventions (see `messaging.md`).

---

## Current state

### Telegram webhook dispatch

The FastAPI webhook route calls `Update.model_validate(body)` then
`await self._dp.feed_update(self._bot, update)` directly — no aiogram
`SimpleRequestHandler`. The route owns parse, validate, dispatch, and response. Secret
token validation runs as a FastAPI `Depends` verifier. The test harness drives the ASGI
app via `httpx.AsyncClient` with `ASGITransport`; this pattern must be preserved.
`aiogram.SimpleRequestHandler` is an available migration path if the test harness is
reworked, but is not adopted. → ADR-003

### CLI entry-point dispatch

Agent management commands (`create`, `list`, `validate`, etc.) are exposed via a
dedicated `[project.scripts]` entry in `pyproject.toml`:

```
lyra-agent = "lyra.cli:agent_main"
```

`__main__.py` is single-purpose (daemon bootstrap) and untouched by CLI dispatch.
`cli.py` is the sole home of CLI logic and is independently testable without `sys.argv`
patching. Invocation is `uv run lyra-agent <sub-command>`, consistent with the
`voicecli` / `imagecli` conventions. supervisord, `make lyra`, and the production deploy
path are unaffected. → ADR-020

### Media temp-file lifecycle

Temp files for binary media (audio, images) are created by the adapter and cleaned up by
the agent's `process()` method via `try/finally` at the `STTService.transcribe()` call
site. `STTService` does not delete files it did not create. This pattern extends to all
future media types: any agent method that receives a binary media path owns cleanup.
Temp files are created with `tempfile.mkstemp(suffix=".ogg", dir=<LYRA_AUDIO_TMP>)`.
A startup-time stale-file sweep of `LYRA_AUDIO_TMP` is recommended as a safety net for
crash-orphaned files (deferred). → ADR-013

### Inbound audio routing

`InboundAudio` was formerly a dead type — produced by both adapters via `normalize_audio()`
but never enqueued, with audio repacked redundantly into `InboundMessage + Attachment`.
This double-normalization is resolved: `InboundAudio` was superseded by `AudioPayload`
(`src/lyra/core/audio_payload.py`), which both adapters produce and `AudioPipeline`
consumes. `normalize_audio()` is present on the `ChannelAdapter` Protocol
(`hub_protocol.py:36`). Open items: `start()` / `stop()` lifecycle methods are still
absent from the Protocol; `platform_meta: dict` → typed `PlatformContext` migration is
partial (hard prerequisite before a third platform is added). → ADR-014

### Outbound audio dispatch & reply-id

Four findings from the Phase 1b review, resolved as of 2026-05-08:

- **A (render_audio dispatch gap):** Audio dispatch now routes through `AudioPipeline`
  wired into `OutboundRouter` — no longer bypasses `OutboundDispatcher`.
- **B (send_streaming reply_message_id):** Option B1 (thread `OutboundMessage` through
  `send_streaming()`) adopted; implementation tracked in issue #67 — status unclear.
- **C (OutboundAudio mutability):** `OutboundAudio` is `@dataclass(frozen=True)` at
  `src/lyra/core/messaging/message.py:160–168`, consistent with all other envelope types.
- **D (adapter backpressure inconsistency):** Per-scope lock fan-out with `_scope_locks`
  and `_SCOPE_REAP_THRESHOLD` in `outbound_dispatcher.py:177–212`.

→ ADR-015

### TTS prefs overlay

TTS resolution chain per call: **user prefs** → **STT-detected language**
(`InboundMessage.language`) → **agent default** → **service default**. `PrefsStore`
is application-layer (hub DI); it resolves via `prefs_store.get_prefs(msg.user_id)`
inside `_synthesize_and_dispatch_audio`. One `TTSService` per process (shared GPU
resource). Per-agent TTS config is honoured via per-call `language`/`voice` kwargs on
`TTSService.synthesize()` (Option B); per-agent `ProviderRegistry` is live in
`bootstrap/factory/agent_factory.py`. `prefs_store.close()` must be called in the
graceful shutdown path. `InboundMessage.language` is a hint field only — never used for
routing or trust. → ADR-023

### STT/TTS NATS decoupling

`lyra_stt` and `lyra_tts` run as independent NATS adapter services alongside
`lyra_hub`, `lyra_telegram`, and `lyra_discord`. The hub never imports `voicecli`.
`AudioPipeline` calls `NatsSttClient.transcribe()` and `NatsTtsClient.synthesize()`
over NATS request-reply (`lyra.voice.stt.request` / `lyra.voice.tts.request`). Both
clients satisfy `STTProtocol` / `TtsProtocol` structural interfaces — drop-in for the
old `STTService` / `TTSService`. On NATS timeout, `STTUnavailableError` is raised;
`AudioPipeline` treats it identically to `stt is None` (sends `stt_unavailable` reply).
Hub starts and processes text immediately regardless of whether voice adapters are up.
Deployment via Quadlet units (`deploy/quadlet/lyra-stt.container`,
`deploy/quadlet/lyra-tts.container`). → ADR-039

---

## Key invariants

- The Telegram webhook route calls `feed_update()` directly; it does not delegate to
  `aiogram.SimpleRequestHandler`.
- `__main__.py` is daemon bootstrap only; all CLI dispatch lives in `cli.py`.
- The agent's `process()` owns temp file cleanup via `try/finally`; services never
  delete files they did not create.
- Audio bytes never travel through the hub as a blocking in-process call; all
  transcription and synthesis is via NATS request-reply to isolated adapter processes.
- `OutboundAudio` is frozen (`@dataclass(frozen=True)`); all inbound envelopes are also
  frozen.
- `InboundMessage.language` is a TTS hint only — never a routing or trust input.
- `platform_meta: dict` is the current inbound routing escape hatch; typed
  `PlatformContext` migration is required before any third platform is added.
- Hub starts and serves text traffic independently of voice adapter availability.
- `prefs_store.close()` must be in the graceful shutdown sequence.

---

## Open questions / known gaps

- `send_streaming()` reply-threading (Finding B / issue #67): `OutboundMessage` thread
  through `send_streaming()` adopted but implementation status unclear.
- `ChannelAdapter` Protocol missing `start()` / `stop()` lifecycle methods (ADR-014
  Option D); render_audio() is present but lifecycle is not.
- `platform_meta: dict` → `PlatformContext` typed migration is partial; hard prerequisite
  before a third platform adapter is added.
- `OutboundDispatcher._queue` is unbounded; `queue_maxsize` constructor parameter and a
  sensible default are recommended but not yet added.
- Startup-time stale temp-file sweep of `LYRA_AUDIO_TMP` on hub restart is deferred.
- Multi-agent `AgentTTSConfig` — until Option B is verified at `AudioPipeline` /
  `TTSService` call sites, multi-agent deployments should log a warning when two agents
  differ in `AgentTTSConfig`.

---

## See also

- CliPool worker → `workers-tooling.md` (ADR-004, ADR-071)
- Routing key → `messaging.md` (ADR-001)
- TTS/STT NATS contracts → `contracts.md` (ADR-052)

---

## ADR archive

| ADR | Title | Status |
|-----|-------|--------|
| 003 | Telegram webhook dispatch strategy | Accepted |
| 013 | Media temp-file lifecycle ownership | Accepted |
| 014 | Adapter protocol gaps and inbound audio routing | Accepted |
| 015 | Outbound audio dispatch gap and streaming reply-id | Accepted (2026-05-08) |
| 020 | CLI entry-point dispatch strategy | Accepted (2026-05-08) |
| 023 | Per-user TTS prefs and agent TTS config overlay | Accepted (2026-05-08) |
| 039 | STT/TTS NATS adapter decoupling | Accepted |
