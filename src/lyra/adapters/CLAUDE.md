# src/lyra/adapters/ — Channel Adapters

## Purpose

Translate platform-native events → `InboundMessage` / `AudioPayload` and
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
- Compose typing `factory_builder` via `lyra.typing.make_typing_factory(worker_fn)` —
  defined in `src/lyra/typing/listener.py`. Never hand-write a per-adapter
  `_build_*_typing_factory` closure. Stage-axis helper avoids N×M drift (N platforms
  × M typing concerns). Watch-trigger: a 3rd platform or a 2nd typing concern (e.g.
  per-platform throttle) crosses the ADR-073 target-axis-trap threshold and must
  reuse the helper, not duplicate the closure. Every adapter `_start_typing()` must
  call `self._typing.start(scope_id, self._factory_builder(scope_id))` — no lambdas
  or closures may survive in that method body; grep for `lambda` inside
  `_start_typing` is a fast negative signal.

## ChannelAdapter protocol (`core/hub/hub_protocol.py`)

Role interface (Fowler) — the hub's view of every platform adapter: inbound normalization, outbound delivery, audio, attachments. See `src/lyra/core/hub/hub_protocol.py` for the full Protocol definition.

`render_voice_stream()` on Telegram logs a warning and returns — voice-channel
playback is Discord-only. Do NOT make it functional.

## OutboundAdapterBase (`shared/_base_outbound.py`)

Inherit for every new platform adapter. Abstract methods to implement:

| Method | Role |
|--------|------|
| `send(original_msg, outbound)` | Send complete reply |
| `_make_emitter(original_msg, outbound) -> OutboundEmitter` | Compose stage objects (formatter + throttle + error_handler) |
| `_start_typing(scope_id)` | Start typing indicator |
| `_cancel_typing(scope_id)` | Cancel typing indicator |

`send_streaming()` is **concrete** on the base — delegates to `_make_emitter()`
which returns an `OutboundEmitter`. Do NOT override `send_streaming()`. Platform
differences belong in `_make_emitter()` (stage composition) and the per-platform
formatter/typing-indicator implementations under `outbound/`.

`OutboundAdapterBase` has no `__init__` intentionally. Do NOT add one — it breaks
cooperative MRO with `discord.Client`.

`configure_tool_display(config: ToolDisplayConfig | None)` is the **single permitted
per-instance write point** for `tool_display_config` (stored as
`self._tool_display_config`). It is a **post-construction setter** — bootstrap calls
it after construction, keeping per-instance config storage off `__init__`. Every
adapter inherits it. A new platform adapter MUST NOT re-declare a
`tool_display_config` constructor kwarg or a bare
`self._tool_display_config = ...` assignment. Same "define once on the base, never
per-adapter-dir" rationale as `make_typing_factory` — avoids N×M drift per
ADR-073.

## MRO constraint (Discord only)

`discord.Client` must be first in `DiscordAdapter(discord.Client, OutboundAdapterBase)`. See `src/lyra/outbound/CLAUDE.md` — "OutboundAdapterBase has NO `__init__`".

## OutboundFormatter / ThrottleCapability / OutboundErrorHandler stages (`src/lyra/outbound/`)

Per-platform code is thin formatter implementations (`telegram_formatter.py`,
`discord_formatter.py` ≤200 LOC each) plus thin typing-indicator wrappers
(`TelegramTypingIndicator`, `DiscordTypingIndicator`).

| Stage | Role |
|-------|------|
| Emitter | Composes formatter + throttle + error_handler; owns placeholder→edits→delivery |
| Formatter | pure formatting (chunk, render_text, etc.) + platform-I/O mechanics (send_placeholder, send_message, etc.) |
| Throttle | start_typing/cancel_typing + edit_interval_s |
| Error handler | guard (single broad-catch site), handle, classify_stream_error, get_msg |

`OutboundFormatter` is the single platform-I/O surface consumed by `OutboundEmitter`.
→ `src/lyra/outbound/CLAUDE.md` for stage invariants and Format-vs-I/O split decision.

## Clipool adapter (`clipool/`)

Not a platform adapter — the **NATS worker** that hosts `CliPool` in a separate
process. Hub sends LLM requests via `LlmClient` (composed from `WorkerPoolClient` +
`CliNatsCodec` over NATS request-reply); worker runs the Claude CLI subprocess.
Enables independent lifecycle and horizontal scaling.

NATS subjects:
- `lyra.clipool.cmd` — LLM requests from hub
- `lyra.clipool.control` — control commands (reset, heartbeat)
- `lyra.clipool.heartbeat` — periodic health announcements

## Pipeline stages

Inbound stages (parse, route, session, dispatch) live in `src/lyra/inbound/` — see
`src/lyra/inbound/CLAUDE.md`. Per-platform helpers (audio, voice, thread
management, formatting, normalization) stay here in `adapters/{platform}/`.

Adapter `handle_message` is reduced to: bot filter → platform-only short-circuits
(audio, voice command) → `pipeline.run(raw, ctx, parser, …)`. Hooks needed by a
single platform live in that adapter module (e.g. `_discord_pre_route_hook`,
`_discord_pre_session_hook`) and are bound via `functools.partial`.

## Telegram vs Discord — non-obvious differences

| Aspect | Telegram | Discord |
|--------|----------|---------|
| Transport | HTTP webhooks (FastAPI) | Gateway WebSocket (discord.py) |
| Thread model | `reply_to_message_id` | Discord threads; restored on reconnect |
| Voice | Audio notes only | Full voice channel (`VoiceSessionManager`) |

## NATS contracts

→ ADR-045 (NATS transport), ADR-049 (contract schemas),
`packages/roxabi-nats/`, `packages/roxabi-contracts/`
