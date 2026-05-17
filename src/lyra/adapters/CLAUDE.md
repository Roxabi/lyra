# src/lyra/adapters/ — Channel Adapters

## Purpose

Translate platform-native events → `InboundMessage` / `InboundAudio` and
`OutboundMessage` / `OutboundAudio` → platform API calls.
No business logic, LLM calls, or agent logic lives here.

## Layer invariants

- Always push inbound via `push_to_hub_guarded()` — never `hub.push()` directly.
- Verify sender identity at the platform boundary before constructing `InboundMessage`.
  - Telegram: HMAC on `X-Telegram-Bot-Api-Secret-Token`.
  - Discord: discord.py authenticates; `message.author` is trusted.
- Never derive `user_id` / `scope_id` from unverified payload fields.
- All I/O is async — never block the event loop.
- Formatting logic belongs in `{platform}_formatting.py`; never inline it in inbound or outbound.
- Do NOT use `async with channel.typing():` on Discord — it auto-refreshes and triggers 429s.
  Call `await channel.typing()` manually every 9 s (`_discord_typing_worker`).

## ChannelAdapter protocol (`core/hub/hub_protocol.py`)

| Method | Role |
|--------|------|
| `normalize(raw)` | Raw payload → `InboundMessage` |
| `normalize_audio(raw, bytes, mime, trust_level)` | Raw audio → `InboundAudio` |
| `send(original_msg, outbound)` | Send complete reply |
| `send_streaming(original_msg, chunks, outbound)` | Stream reply with edit-in-place |
| `render_audio(msg, inbound)` | Send voice note |
| `render_audio_stream(chunks, inbound)` | Stream TTS audio chunks |
| `render_attachment(msg, inbound)` | Send attachment |

`render_voice_stream()` is an intentional no-op stub on Telegram — voice-channel
playback is Discord-only. Do NOT make it functional.

## OutboundAdapterBase (`shared/_base_outbound.py`)

Inherit for every new platform adapter. Abstract methods to implement:

| Method | Role |
|--------|------|
| `send(original_msg, outbound)` | Send complete reply |
| `_make_streaming_callbacks(original_msg, outbound) -> PlatformCallbacks` | Build platform callbacks |
| `_start_typing(scope_id)` | Start typing indicator |
| `_cancel_typing(scope_id)` | Cancel typing indicator |

`send_streaming()` is **concrete** on the base — delegates to `StreamingSession`.
Do NOT override it. Platform differences belong in `_make_streaming_callbacks()`.

`OutboundAdapterBase` has no `__init__` intentionally. Do NOT add one — it breaks
cooperative MRO with `discord.Client`.

## MRO constraint (Discord only)

`discord.Client` must be first:

```python
class DiscordAdapter(discord.Client, OutboundAdapterBase):
    def __init__(self, ...):
        super().__init__(intents=intents)  # flows to discord.Client
```

## PlatformCallbacks contract (`shared/_shared_streaming_emitter.py`)

| Field | Role |
|-------|------|
| `send_placeholder` | Send initial placeholder message |
| `edit_placeholder_text` | Edit placeholder with intermediate text |
| `send_message` | Send new message (tool-using turns) |
| `send_fallback` | Fallback send when placeholder fails |
| `chunk_text` | Split text into platform-sized chunks |
| `start_typing` / `cancel_typing` | Typing indicator lifecycle |
| `send_trace_placeholder` | Send reasoning-trace placeholder |
| `edit_trace` | Edit reasoning-trace placeholder (vestigial — see #1214/#1102) |
| `edit_reasoning` | Render reasoning Start/Delta/End |
| `edit_tool_recap` | Render tool recap card lines (debounced + final) |
| `get_msg` | i18n message lookup |
| `placeholder_text` | Initial placeholder text |

`_tool_recap.py` — `ToolRecapAccumulator` / `format_recap_lines()` backing
`edit_tool_recap` above.

## Clipool adapter (`clipool/`)

Not a platform adapter — the **NATS worker** that hosts `CliPool` in a separate
process. Hub sends LLM requests via `CliNatsDriver`; worker runs the Claude CLI
subprocess. Enables independent lifecycle and horizontal scaling.

NATS subjects:
- `lyra.clipool.cmd` — LLM requests from hub
- `lyra.clipool.control` — control commands (reset, heartbeat)
- `lyra.clipool.heartbeat` — periodic health announcements

## Telegram vs Discord — non-obvious differences

| Aspect | Telegram | Discord |
|--------|----------|---------|
| Transport | HTTP webhooks (FastAPI) | Gateway WebSocket (discord.py) |
| Thread model | `reply_to_message_id` | Discord threads; restored on reconnect |
| Voice | Audio notes only | Full voice channel (`VoiceSessionManager`) |

## NATS contracts

→ ADR-045 (NATS transport), ADR-049 (contract schemas),
`packages/roxabi-nats/`, `packages/roxabi-contracts/`
