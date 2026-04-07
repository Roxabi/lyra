# src/lyra/adapters/ — Channel Adapters (Telegram + Discord)

## Purpose

Each file in `adapters/` implements the `ChannelAdapter` protocol for one platform.
Adapters translate platform-native events into `InboundMessage` / `InboundAudio`
and translate `OutboundMessage` / `OutboundAudio` into platform API calls.
No business logic or LLM interaction lives here.

## ChannelAdapter protocol (defined in `core/hub/hub_protocol.py`)

Every adapter must implement:

| Method | Role |
|--------|------|
| `normalize(raw)` | Parse a raw platform payload into `InboundMessage` |
| `normalize_audio(raw, bytes, mime, trust_level)` | Parse audio payload into `InboundAudio` |
| `send(original_msg, outbound)` | Send a complete reply |
| `send_streaming(original_msg, chunks, outbound)` | Stream reply with edit-in-place |
| `render_audio(msg, inbound)` | Send a voice note |
| `render_audio_stream(chunks, inbound)` | Stream TTS audio chunks |
| `render_attachment(msg, inbound)` | Send an attachment (image/file) |

`render_voice_stream()` is implemented as a no-op stub on Telegram (drains the
iterator and logs a warning); functional voice-channel playback is Discord-only.

## Non-obvious structure notes

Each platform is split into focused submodules: `{platform}.py` is the facade; concerns are `_inbound`, `_outbound`, `_normalize`, `_formatting`, `_audio`. Shared cross-platform code lives in `_shared.py`, `_shared_audio.py`, `_shared_streaming.py`.

`outbound_listener.py` defines a structural protocol — adapters reference it without inheriting. `NatsOutboundListener` satisfies it for the three-process NATS deployment mode.

## Telegram vs Discord differences

| Aspect | Telegram | Discord |
|--------|----------|---------|
| Transport | HTTP webhooks (FastAPI) | Gateway WebSocket (discord.py) |
| Streaming edit interval | 1 s | 1 s |
| Typing indicator | `send_chat_action` every 3 s | `trigger_typing()` every 9 s |
| Thread model | Replies use `reply_to_message_id` | Uses Discord threads; restored on reconnect |
| Voice | Audio notes only | Full voice channel with VoiceSessionManager |
| Auth | Webhook secret via HMAC | Bot token via env; no webhook |
| Max message length | ~4096 chars (Telegram limit) | `DISCORD_MAX_LENGTH` (~2000) |

## Outbound patterns

All outbound code shares this pattern:

1. `send()` — for complete replies: cancel typing indicator, render text/buttons,
   call platform API once.
2. `send_streaming()` — for streaming replies: cancel typing on first chunk, edit
   message in place at `STREAMING_EDIT_INTERVAL` (1 s) debounce, finalize.

Streaming edit-in-place: the adapter sends a placeholder message on the first chunk
and edits it with accumulated text as more chunks arrive. The final edit contains
the complete response.

When `outbound` is passed to `send_streaming()`, the adapter writes the platform
message ID to `outbound.metadata["reply_message_id"]` after sending.

## OutboundAdapterBase

`_base_outbound.py` defines the shared outbound contract for all platform adapters.
Inherit this base whenever you add a new platform adapter.

### Abstract methods (must implement)

| Method | Signature | Role |
|--------|-----------|------|
| `send` | `async (original_msg, outbound) -> None` | Send a complete reply |
| `_make_streaming_callbacks` | `(original_msg, outbound) -> PlatformCallbacks` | Build platform callbacks |
| `_start_typing` | `(scope_id: int) -> None` | Start typing indicator |
| `_cancel_typing` | `(scope_id: int) -> None` | Cancel typing indicator |

### Concrete method (do NOT override)

`send_streaming(original_msg, events, outbound=None)` — provided by the base; creates a
`StreamingSession` with your `PlatformCallbacks` and runs the shared algorithm.

### PlatformCallbacks fields (`_shared_streaming.py`)

| Field | Type | Role |
|-------|------|------|
| `send_placeholder` | `async () -> (obj, id\|None)` | Send initial placeholder message |
| `edit_placeholder_text` | `async (obj, text) -> None` | Edit placeholder with intermediate text |
| `edit_placeholder_tool` | `async (obj, event, header) -> None` | Edit placeholder with tool summary |
| `send_message` | `async (text) -> id\|None` | Send new message (tool-using turns) |
| `send_fallback` | `async (text) -> id\|None` | Fallback send when placeholder fails |
| `chunk_text` | `(text) -> list[str]` | Split text into platform-sized chunks |
| `start_typing` | `() -> None` | Start typing indicator (sync) |
| `cancel_typing` | `() -> None` | Cancel typing indicator (sync) |

### MRO pattern for discord.Client

Discord requires `discord.Client` first in the MRO:

```python
class DiscordAdapter(discord.Client, OutboundAdapterBase):
    def __init__(self, ...):
        super().__init__(intents=intents)  # flows to discord.Client
        # OutboundAdapterBase has no __init__ — no call needed
```

**`__init__` constraint:** `OutboundAdapterBase` intentionally has no `__init__`.
Do NOT add one — it breaks the cooperative `discord.Client` chain.

### Adding a new platform adapter

```python
class MyAdapter(OutboundAdapterBase):
    async def send(self, original_msg, outbound): ...

    def _make_streaming_callbacks(self, original_msg, outbound) -> PlatformCallbacks:
        return PlatformCallbacks(
            send_placeholder=...,
            edit_placeholder_text=...,
            edit_placeholder_tool=...,
            send_message=...,
            send_fallback=...,
            chunk_text=...,
            start_typing=...,
            cancel_typing=...,
        )

    def _start_typing(self, scope_id): ...
    def _cancel_typing(self, scope_id): ...
```

## Security contract

Adapters must verify sender identity at the platform level before constructing an
`InboundMessage`. The hub trusts `user_id` and `scope_id` from the message object.

- Telegram: validate `X-Telegram-Bot-Api-Secret-Token` header via HMAC.
- Discord: discord.py validates the connection; `message.author` is authenticated.

Never derive `user_id` or `scope_id` from unverified fields in the raw payload.

## Shared helpers (`_shared.py`)

`push_to_hub_guarded()` is the single entry point for all inbound push operations.
It handles:
- Circuit breaker open → drop with backpressure response
- Hub queue full (backpressure) → warn user

Always use `push_to_hub_guarded()` instead of calling `hub.push()` directly.

`TypingTaskManager` manages the lifecycle of the typing indicator task. Start it
when a message is received; cancel it when the reply is sent.

## Conventions

- The facade (`telegram.py`, `discord.py`) only imports from submodules — no logic.
- Submodules are named `{platform}_{concern}.py` — keep this naming consistent.
- Audio size limit: `_MAX_OUTBOUND_AUDIO_BYTES` from `_shared_audio.py`. Never
  attempt to send audio above this limit without chunking or rejecting.
- Formatting logic belongs in `{platform}_formatting.py` — not in outbound or inbound.
- `chunk_text()` splits long text for platforms with message length limits.

## What NOT to do

- Do NOT add LLM calls, agent logic, or memory reads to adapters.
- Do NOT call `hub.push()` directly — use `push_to_hub_guarded()`.
- Do NOT make `render_voice_stream()` functional in the Telegram adapter — it is
  intentionally a no-op stub; voice-channel playback is Discord-only.
- Do NOT add platform-specific constants to `_shared.py` — put them in the
  platform-specific submodule.
- Do NOT block the event loop in any adapter method — all I/O must be async.
- Do NOT use `async with channel.typing():` on Discord — the context manager auto-refreshes
  every 5 s and triggers 429s under load. Instead call `await channel.typing()` manually
  every 9 s (see `_discord_typing_worker`).
- Do NOT override `send_streaming()` in a concrete adapter — it is a concrete method on
  `OutboundAdapterBase` that delegates to `StreamingSession`. Platform differences belong
  in `_make_streaming_callbacks()`, not in a `send_streaming` override.
