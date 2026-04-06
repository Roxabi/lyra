# NATS Topic Topology — Lyra — 2026-04-06

## Subject Naming Convention

The Lyra NATS topology follows this pattern:

```
lyra.<direction>.<subsystem>.<platform/type>.<bot_id|request_type>
```

**Directions:**
- `inbound` — adapters → hub (platform messages)
- `outbound` — hub → adapters (responses)
- `voice` — hub ↔ voice workers (STT/TTS request-reply)
- `system` — hub internal signals (readiness, shutdown)

**Subsystems:**
- `inbound` — unified message flow
- `inbound.audio` — legacy audio-only messages (deprecated, phase-out in #534)
- `outbound` — response dispatch
- `voice.stt` — speech-to-text requests
- `voice.tts` — text-to-speech requests
- `system.ready` — hub readiness probe

---

## Complete Topic Map

| Subject Pattern | Direction | Publisher | Subscriber | Queue Group | Notes |
|---|---|---|---|---|---|
| `lyra.inbound.{platform}.{bot_id}` | adapter→hub | Adapter | NatsBus (Hub) | `hub-inbound` | Main unified inbound text messages. Platform ∈ {`telegram`, `discord`} |
| `lyra.inbound.audio.{platform}.{bot_id}` | adapter→hub | Adapter (legacy) | InboundAudioLegacyHandler | `hub-inbound` | **DEPRECATED** — audio messages still on old code. Phase-out in #534 (Slice 2). Converted to `InboundMessage(modality="voice")` |
| `lyra.outbound.{platform}.{bot_id}` | hub→adapter | NatsChannelProxy (Hub) | NatsOutboundListener (Adapter) | `adapter-outbound-{platform}-{bot_id}` | All outbound envelopes: `send`, `stream_start`, chunk events, `stream_error`, `attachment` |
| `lyra.voice.stt.request` | hub→worker | NatsSttClient (Hub) | STT adapter | `stt-workers` | Request-Reply. Hub sends audio; worker transcribes and replies on `msg.reply` |
| `lyra.voice.tts.request` | hub→worker | NatsTtsClient (Hub) | TTS adapter | `tts-workers` | Request-Reply. Hub sends text; worker synthesizes and replies on `msg.reply` |
| `lyra.system.ready` | adapter→hub | Readiness probe (Adapter) | readiness_responder (Hub) | (none) | Request-Reply. Adapter polls hub status during startup; hub replies with uptime + bus count |

---

## Process Topology

```
┌────────────────────────────────────────────────────────────────────────┐
│                           NATS Cluster                                 │
├────────────────────────────────────────────────────────────────────────┤
│                                                                        │
│   lyra.inbound.telegram.bot-a  ◄──────────────────┐                  │
│   lyra.inbound.discord.bot-b   ◄──────────────────┤                  │
│   lyra.inbound.audio.*.* ─────────────────────┐   │                  │
│                                               │   │                  │
│   lyra.outbound.telegram.bot-a ───────────────┼───┼──────►           │
│   lyra.outbound.discord.bot-b  ───────────────┼───┼──────►           │
│                                               │   │                  │
│   lyra.voice.stt.request ◄───────────────────┼───┼──────────┐        │
│   lyra.voice.tts.request ◄───────────────────┼───┼──────────┤        │
│                                               │   │          │        │
│   lyra.system.ready ◄─────────────────────────┼───┼──────────┤        │
│                                               │   │          │        │
└───────────────────────────────────────────────┼───┼──────────┼────────┘
                                                │   │          │
    ┌─────────────────────────────────────────┘   │          │
    │                                             │          │
    ▼                                             │          │
┌──────────────────────────┐                      │          │
│    lyra_hub              │                      │          │
│  (unified process)       │                      │          │
│                          │                      │          │
│ ┌──────────────────────┐ │   ┌────────────────┐│         │
│ │ NatsBus[InboundMsg]  │ │◄──┤ TelegramAdapter ├─────────┘
│ │ (queue_group:        │ │   └────────────────┘         (publish inbound)
│ │  hub-inbound)        │ │                             (subscribe outbound)
│ │                      │ │   ┌────────────────┐
│ │ (legacy compat shim) │ │◄──┤ DiscordAdapter  ├─────────┐
│ │ Audio→InboundMsg     │ │   └────────────────┘         │
│ └──────────────────────┘ │                               │
│                          │   ┌─────────────────────────┐ │
│ ┌──────────────────────┐ │   │ OutboundDispatcher[*]   │ │
│ │  Hub.run()           │ │   │ (NatsChannelProxy)      │ │
│ │ - recv inbound       │ │   │ - publish outbound      │ │
│ │ - process message    │ │───│ - call adapter methods  │ │
│ │ - dispatch agent     │ │   │ - stream error handling │ │
│ │ - send outbound      │ │   │ (queue: adapter-       │ │
│ └──────────────────────┘ │   │  outbound-{platform})  │ │
│                          │   └─────────────────────────┘ │
│ ┌──────────────────────┐ │                               │
│ │ NatsSttClient        │ │   ┌────────────────┐          │
│ │ NatsTtsClient        │ │   │ STT/TTS Adapter│          │
│ │ (request-reply on    │ │   │ (standalone    │          │
│ │  lyra.voice.*)       │ │───│  process or    │          │
│ │                      │ │   │  supervisor)   │          │
│ │ Readiness responder  │ │   └────────────────┘          │
│ │ (lyra.system.ready)  │ │                               │
│ └──────────────────────┘ │   ┌────────────────┐          │
└──────────────────────────┘   │ Readiness      │          │
                               │ Prober (Adapter)          │
                               │ (startup only) │          │
                               └────────────────┘          │
```

---

## Queue Groups

Queue groups enforce load balancing so no message is processed twice during rolling restarts.

| Queue Group | Role | Subscriber(s) | Subject(s) | Notes |
|---|---|---|---|---|
| `hub-inbound` | Hub-side inbound text & audio | NatsBus + InboundAudioLegacyHandler | `lyra.inbound.{platform}.{bot_id}` + `lyra.inbound.audio.>` | Only one hub instance receives each inbound message |
| `adapter-outbound-{platform}-{bot_id}` | Adapter-side outbound dispatch | NatsOutboundListener | `lyra.outbound.{platform}.{bot_id}` | Only one adapter instance for a given platform+bot_id receives each outbound envelope |
| `stt-workers` | STT worker pool | STT adapter(s) | `lyra.voice.stt.request` | Multiple STT workers share request load |
| `tts-workers` | TTS worker pool | TTS adapter(s) | `lyra.voice.tts.request` | Multiple TTS workers share request load |

---

## Request-Reply Patterns

### STT Request-Reply: `lyra.voice.stt.request`

**Initiator:** `NatsSttClient` (Hub)

**Request payload:**
```json
{
  "request_id": "uuid",
  "audio_b64": "base64-encoded audio data",
  "mime_type": "audio/ogg",
  "model": "large-v3-turbo",
  "language_detection_threshold": null,
  "language_detection_segments": null,
  "language_fallback": null
}
```

**Response payload** (on `msg.reply`):
```json
{
  "request_id": "uuid",
  "ok": true,
  "text": "transcribed text",
  "language": "en",
  "duration_seconds": 2.5
}
```

**Error response:**
```json
{
  "request_id": "uuid",
  "ok": false,
  "error": "transcription_failed"
}
```

**Timeout:** 60 seconds (configurable in `NatsSttClient.__init__`)

**Queue group:** `stt-workers` (multiple repliers allowed; first response wins)

---

### TTS Request-Reply: `lyra.voice.tts.request`

**Initiator:** `NatsTtsClient` (Hub)

**Request payload:**
```json
{
  "request_id": "uuid",
  "text": "text to synthesize",
  "language": "en",
  "voice": "en-US-Neural2-A",
  "fallback_language": "en",
  "chunked": true,
  "engine": "google",
  "accent": null,
  "personality": null,
  "speed": 1.0,
  "emotion": null,
  "exaggeration": null,
  "cfg_weight": null,
  "segment_gap": null,
  "crossfade": null,
  "chunk_size": null
}
```

**Response payload** (on `msg.reply`):
```json
{
  "request_id": "uuid",
  "ok": true,
  "audio_b64": "base64-encoded audio data",
  "mime_type": "audio/ogg",
  "duration_ms": 3500,
  "waveform_b64": "optional waveform visualization"
}
```

**Error response:**
```json
{
  "request_id": "uuid",
  "ok": false,
  "error": "synthesis_failed"
}
```

**Timeout:** 30 seconds (configurable in `NatsTtsClient.__init__`)

**Queue group:** `tts-workers`

---

### Readiness Probe: `lyra.system.ready`

**Initiator:** Adapter startup (via `wait_for_hub()`)

**Request:** Empty body (`b""`)

**Response payload** (on `msg.reply`):
```json
{
  "status": "ready",
  "uptime_s": 123.456,
  "buses": 2
}
```

**Probe interval:** 0.5 seconds
**Probe timeout:** 30 seconds (configurable in `wait_for_hub(timeout=...)`)

**Responder:** `start_readiness_responder()` (Hub's `readiness_sub`)

**Purpose:** Adapters poll the hub until it confirms all NATS subscriptions are active.

---

## Outbound Envelope Types

All envelopes are published to `lyra.outbound.{platform}.{bot_id}` with queue group `adapter-outbound-{platform}-{bot_id}`.

Each envelope is a JSON object with a top-level `type` field:

### Envelope: `type: "send"`
Single non-streaming response.

```json
{
  "type": "send",
  "stream_id": "message-uuid",
  "outbound": { /* OutboundMessage JSON */ }
}
```

**Dispatcher:** `NatsChannelProxy.send()`

---

### Envelope: `type: "stream_start"`
Metadata for the start of a streaming response.

```json
{
  "type": "stream_start",
  "stream_id": "message-uuid",
  "outbound": { /* OutboundMessage JSON */ }
}
```

**Dispatcher:** `NatsChannelProxy.send_streaming()` (on first chunk)

**Purpose:** Allows adapter to set up streaming session and capture the reply message ID.

---

### Envelope: `type: "stream_end"` (synthetic)
Terminal sentinel indicating all chunks have been published.

```json
{
  "stream_id": "message-uuid",
  "seq": 42,
  "event_type": "stream_end",
  "payload": {},
  "done": true
}
```

**Dispatcher:** `NatsChannelProxy.send_streaming()` (always published last)

**Purpose:** Ensures adapter's streaming loop terminates cleanly even if the events iterator was empty.

---

### Envelope: `type: "stream_error"`
Transport error or hub failure during streaming.

```json
{
  "type": "stream_error",
  "stream_id": "message-uuid",
  "reason": "streaming_exception" | "hub_shutdown"
}
```

**Dispatcher:** `NatsChannelProxy.send_streaming()` (on exception) or `publish_stream_errors()` (on hub shutdown)

**Purpose:** Signals adapter to terminate streaming session gracefully.

---

### Envelope: Streaming Chunk (no top-level `type`)
RenderEvent chunk carrying text or tool summary.

```json
{
  "stream_id": "message-uuid",
  "seq": 0,
  "event_type": "text" | "tool_summary",
  "payload": { /* TextRenderEvent or ToolSummaryRenderEvent JSON */ },
  "done": false
}
```

**Dispatcher:** `NatsChannelProxy.send_streaming()` (for each RenderEvent)

**RenderEvent types** (from `render_event_codec.py`):
- `"text"` → `TextRenderEvent`: text chunk with `is_final` flag
- `"tool_summary"` → `ToolSummaryRenderEvent`: tool execution summary with `is_complete` flag
- `"stream_end"` → synthetic sentinel (no payload)
- `"stream_error"` → handled separately (no payload, top-level `type` field)

---

### Envelope: `type: "attachment"`
File or image attachment.

```json
{
  "type": "attachment",
  "stream_id": "message-uuid",
  "attachment": { /* OutboundAttachment JSON */ }
}
```

**Dispatcher:** `NatsChannelProxy.render_attachment()`

---

## Startup Sequence (Topic-Level)

1. **Hub starts** (or restarts)
   - Connects to NATS
   - Creates `NatsBus[InboundMessage]` subscribed to `lyra.inbound.*` (queue: `hub-inbound`)
   - Creates `InboundAudioLegacyHandler` subscribed to `lyra.inbound.audio.>` (queue: `hub-inbound`)
   - Registers `readiness_responder` on `lyra.system.ready`
   - Initializes `NatsSttClient` and `NatsTtsClient`
   - Logs "Hub ready — accepting readiness probes on lyra.system.ready"

2. **Adapters start** (in unified or adapter-standalone mode)
   - Connect to NATS
   - Call `wait_for_hub()` → **polls** `lyra.system.ready` every 0.5 s until hub replies
   - On hub response: proceed with adapter initialization
   - On timeout (30 s): log warning, start anyway (graceful degradation)
   - Register NATS subscription to their outbound subject:
     - Telegram: `lyra.outbound.telegram.{bot_id}` (queue: `adapter-outbound-telegram-{bot_id}`)
     - Discord: `lyra.outbound.discord.{bot_id}` (queue: `adapter-outbound-discord-{bot_id}`)
   - Register as ChannelAdapter in hub's dispatcher map

3. **Voice adapters start** (in standalone mode)
   - STT: subscribe to `lyra.voice.stt.request` (queue: `stt-workers`)
   - TTS: subscribe to `lyra.voice.tts.request` (queue: `tts-workers`)
   - Ready to respond on `msg.reply` to incoming requests

4. **System ready**
   - Hub receives inbound messages on `lyra.inbound.*` and processes them
   - Adapters receive outbound envelopes on `lyra.outbound.*` and dispatch to platform APIs
   - Voice requests routed to STT/TTS workers on request-reply subjects

---

## Gaps / Risks

### Documented

1. **Legacy audio subject** (`lyra.inbound.audio.>`)
   - Status: Phase-out in #534 (Slice 2)
   - Risk: Adapters still on old code will publish here; hub bridges via `InboundAudioLegacyHandler`
   - Mitigation: Compat shim converts to unified `InboundMessage(modality="voice")`
   - Target: Remove entire `nats/compat/` package in Phase 2

2. **Audio-over-NATS (C5)** not yet implemented
   - `NatsChannelProxy.render_audio()` and `render_audio_stream()` are stubs
   - They log warnings and drain iterators without publishing
   - Voice playback is Discord-only via Discord voice channel (not NATS)
   - Future: Design and implement audio streaming envelope format

### Potential Issues

3. **Subject token validation**
   - `bot_id` and `platform` are user-configurable
   - `NatsBus` validates via `validate_nats_token()`
   - Risk: Invalid tokens crash at startup (good fail-fast)
   - Mitigation: Config validation in bootstrap phase

4. **Readiness probe timeout**
   - Default 30 s with 0.5 s polling interval
   - If hub doesn't respond, adapters start anyway
   - Risk: Inbound messages lost during race if adapter subscribes before hub is ready
   - Mitigation: Queue group `hub-inbound` ensures only one hub processes; adapters delay publish until ready

5. **Streaming session cleanup**
   - `NatsOutboundListener` evicts stale cache entries (120 s TTL) via `_reap_stale()`
   - If a stream_id is orphaned (e.g., hub crash), it lingers until TTL expires
   - Risk: Memory leak if many incomplete streams accumulate
   - Mitigation: Monitor `_cache` size and TTL; consider lower TTL for high-traffic deployments

6. **Stream error race conditions**
   - `NatsChannelProxy` can emit `stream_error` on exception while chunks are still inflight
   - `NatsOutboundListener` may receive error before final chunk (see `_handle_stream_error`)
   - Risk: Adapter skips chunk or error is dropped if stream_id not found
   - Mitigation: `stream_error` handler marks stream as terminated; subsequent chunks ignored

7. **Max payload size**
   - NATS `max_payload` config limits request/response size
   - STT: audio can exceed 4 MB; error handling in place
   - TTS: text usually small; large agent_tts config can inflate request
   - Risk: Silent drop or error if payload exceeds server limit
   - Mitigation: Log errors; raise `STTUnavailableError` / `TtsUnavailableError`

8. **No ordering guarantees per platform+bot_id**
   - NATS pub/sub is not ordered across subscribers within a queue group
   - Multiple hub instances (rare) will load-balance inbound messages unpredictably
   - Risk: Out-of-order processing if N > 1 hub instances
   - Mitigation: Single hub per deployment (queue group enforces this at NATS level)

9. **Voice worker queue groups may fan out**
   - If multiple STT/TTS workers join same queue group, load is balanced
   - Currently each request goes to one worker (first responder wins)
   - Risk: Uneven load distribution if workers have different capacity
   - Mitigation: Monitor queue metrics; use NATS Jetstream for fairness (future)

---

## Implementation Details

### NatsBus Subscription Pattern

File: `src/lyra/nats/nats_bus.py`

```python
subject = f"{subject_prefix}.{platform.value}.{bot_id}"
sub = await nc.subscribe(subject, queue=queue_group, cb=handler)
```

- **Default subject_prefix:** `"lyra.inbound"`
- **Default queue_group:** `""` (empty, no queue group)
- **Used by:** Hub's inbound bus with queue_group `HUB_INBOUND` = `"hub-inbound"`

### NatsChannelProxy Publish Pattern

File: `src/lyra/nats/nats_channel_proxy.py`

```python
subject = f"lyra.outbound.{platform.value}.{bot_id}"
await nc.publish(subject, json_payload)
```

- **Subject:** Fixed pattern, no configuration
- **Payload:** Top-level JSON with `type` field or chunk envelope
- **Used by:** Hub's `OutboundDispatcher` → adapter delivery

### NatsSttClient / NatsTtsClient Request-Reply

File: `src/lyra/nats/nats_stt_client.py` / `nats_tts_client.py`

```python
class NatsSttClient:
    SUBJECT = "lyra.voice.stt.request"
    
    async def transcribe(self, path):
        reply = await self._nc.request(self.SUBJECT, payload, timeout=60.0)
        data = json.loads(reply.data)
        return TranscriptionResult(...)
```

- **Pattern:** `nc.request()` is NATS request-reply (internal inbox)
- **Timeout:** Configurable per client instance
- **Error handling:** Wraps NATS timeouts and MaxPayload errors

### Readiness Probe Pattern

File: `src/lyra/nats/readiness.py`

```python
READINESS_SUBJECT = "lyra.system.ready"

async def start_readiness_responder(nc, buses):
    async def handler(msg):
        if not msg.reply:
            return
        uptime_s = time.monotonic() - started_at
        bus_count = sum(b.subscription_count for b in buses)
        await nc.publish(msg.reply, json.dumps({...}).encode())
    
    sub = await nc.subscribe(READINESS_SUBJECT, cb=handler)

async def wait_for_hub(nc, timeout=30.0):
    while time.monotonic() < deadline:
        try:
            await nc.request(READINESS_SUBJECT, b"", timeout=per_call)
            return True
        except (TimeoutError, NoRespondersError):
            await asyncio.sleep(0.5)
    return False
```

---

## Summary

Lyra's NATS topology consists of **6 core subject trees**:

1. **`lyra.inbound.*`** — Unified inbound messages (text, voice, attachments)
2. **`lyra.inbound.audio.*`** — Legacy audio-only (deprecated, phase-out in #534)
3. **`lyra.outbound.*`** — Outbound envelopes (send, stream_start, chunks, errors)
4. **`lyra.voice.stt.request`** — Speech-to-text request-reply
5. **`lyra.voice.tts.request`** — Text-to-speech request-reply
6. **`lyra.system.ready`** — Hub readiness probe (request-reply)

**Queue groups** ensure no message is processed twice during rolling restarts:
- `hub-inbound` — inbound bus
- `adapter-outbound-{platform}-{bot_id}` — outbound per adapter instance
- `stt-workers` — STT worker pool
- `tts-workers` — TTS worker pool

**Request-reply patterns** (NATS internal inbox) are used for synchronous operations:
- STT transcription (60 s timeout)
- TTS synthesis (30 s timeout)
- Hub readiness probe (30 s total with 0.5 s polling)

**Streaming uses pub/sub** with envelope types (`send`, `stream_start`, chunks, `stream_error`) published to a single outbound subject per platform+bot_id, demultiplexed by `stream_id` on the adapter side.

