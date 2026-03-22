# src/lyra/adapters/ — Channel Adapters (Telegram + Discord)

## Purpose

Each file in `adapters/` implements the `ChannelAdapter` protocol for one platform.
Adapters translate platform-native events into `InboundMessage` / `InboundAudio`
and translate `OutboundMessage` / `OutboundAudio` into platform API calls.
No business logic or LLM interaction lives here.

## ChannelAdapter protocol (defined in `core/hub_protocol.py`)

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

`render_voice_stream()` is Discord-only (voice channel playback). Telegram does not
implement it.

## File structure

Each platform is split into focused submodules to keep files small:

```
telegram.py              # Facade: wires everything, hosts FastAPI app
telegram_inbound.py      # Webhook handler: parse → push to hub
telegram_normalize.py    # normalize() and normalize_audio()
telegram_outbound.py     # send() and send_streaming(); typing indicator
telegram_formatting.py   # Markdown rendering, button layout
telegram_audio.py        # Audio download and upload helpers

discord.py               # Facade: discord.py Client subclass
discord_inbound.py       # on_message() event handler
discord_normalize.py     # normalize()
discord_outbound.py      # send() and send_streaming(); typing worker
discord_formatting.py    # Text rendering, embed helpers
discord_audio.py         # Audio download helpers
discord_audio_outbound.py# TTS audio upload to Discord
discord_threads.py       # Thread restoration on reconnect
discord_voice.py         # VoiceSessionManager (voice channel sessions)
discord_voice_commands.py# /join, /leave app command handlers
discord_config.py        # DiscordConfig, load_discord_config()

_shared.py               # Shared helpers: push_to_hub_guarded, chunk_text,
                         # TypingTaskManager, parse_reply_to_id, etc.
_shared_audio.py         # Audio helpers: buffer_audio_chunks, mime_to_ext, etc.
```

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
- Do NOT implement `render_voice_stream()` in the Telegram adapter — it is
  Discord-only.
- Do NOT add platform-specific constants to `_shared.py` — put them in the
  platform-specific submodule.
- Do NOT block the event loop in any adapter method — all I/O must be async.
- Do NOT use `channel.typing()` on Discord — it triggers 429s under load. Use
  `trigger_typing()` in the manual loop pattern (`_discord_typing_worker`).
