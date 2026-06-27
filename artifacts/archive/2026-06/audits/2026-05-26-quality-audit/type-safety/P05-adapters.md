# Type Safety Audit — P05: src/lyra/adapters

**Date:** 2026-05-26
**Scope:** `src/lyra/adapters/**/*.py` (35 files, ~6,814 lines)
**Context:** Epic #1277 stage-axis refactor; prior audit 2026-05-18 covered hexagonal conformance / duplication / dead-code. This audit focuses exclusively on type-safety regressions and gaps.

---

## Summary

- **Any is the dominant gap.** ~52 standalone `Any` annotations (parameters, variables, returns) plus ~10 generic-parameter usages. The vast majority trace to third-party SDK boundaries (aiogram, discord.py, nats-py) where stubs are incomplete or absent.
- **Public API return-type coverage is 100%.** All 153 public functions carry return type hints. No `# type: ignore` regressions since prior audit; only 1 pre-existing defensive import-untyped suppression remains.
- **cast() usages are safe and narrow.** All 4 casts bridge discord.py channel polymorphism or a conditional import fallback; none mask unsoundness.

---

## Findings

| File | Line | Severity | Description | Recommendation |
|---|---|---|---|---|
| `telegram/telegram.py` | 125 | low | `self._bot: Any = None` — aiogram `Bot` type erased | Import under `TYPE_CHECKING` or use `Bot \| None` once stubs are available |
| `telegram/telegram.py` | 126 | low | `self._dp: Any = None` — aiogram `Dispatcher` type erased | Same as above |
| `telegram/telegram.py` | 140 | low | `bot` property returns `Any` | Narrow to `Bot \| None` or a local Protocol |
| `telegram/telegram.py` | 149 | low | `bot` setter accepts `Any` | Narrow to `Bot \| None` |
| `telegram/telegram.py` | 163 | low | `dp` property returns `Any` | Narrow to `Dispatcher \| None` |
| `telegram/telegram.py` | 173 | low | `handle_update` returns `dict[str, Any]` | Replace `Any` with typed response model or `dict[str, str \| bool]` |
| `telegram/telegram.py` | 185 | low | `get_status` returns `dict[str, Any]` | Same as above |
| `telegram/telegram.py` | 228 | low | `_render_buttons` accepts `list[Any]` | Use `list[Button]` once shared contract exists |
| `telegram/telegram.py` | 231 | low | `_on_message` accepts `msg: Any` | Use `aiogram.types.Message` (or Protocol) |
| `telegram/telegram.py` | 234 | low | `_on_voice_message` accepts `msg: Any` | Same as above |
| `telegram/telegram.py` | 239 | low | `normalize` accepts `raw: Any` | Use `aiogram.types.Message` |
| `telegram/telegram.py` | 247 | low | `normalize_audio` accepts `raw: Any` | Same as above |
| `telegram/telegram_outbound.py` | 68 | low | `_typing_worker` accepts `bot: Any` | Narrow to `Bot` or local typing Protocol |
| `telegram/telegram_outbound.py` | 97 | low | `_typing_loop` accepts `bot: Any` | Same as above |
| `telegram/telegram_outbound.py` | 229 | medium | `_send_placeholder` returns `tuple[Any, int]` | Replace `Any` with `aiogram.types.Message` |
| `telegram/telegram_outbound.py` | 237 | medium | `_edit_placeholder_text` accepts `ph: Any` | Same as above |
| `telegram/telegram_outbound.py` | 250 | medium | `_send_trace_placeholder` returns `tuple[Any, int \| None]` | Same as above |
| `telegram/telegram_outbound.py` | 290 | medium | `_edit_tool_recap` accepts `trace_obj: Any` | Same as above |
| `telegram/telegram_outbound.py` | 312 | medium | `_edit_trace_with_text` accepts `trace_obj: Any` | Same as above |
| `telegram/telegram_outbound.py` | 327 | medium | `_render_reasoning` accepts `trace_obj: Any` | Same as above |
| `telegram/telegram_formatter.py` | 62 | medium | `render_buttons` accepts `buttons: Any` and returns `Any` | Use `list[Button] -> InlineKeyboardMarkup \| None` |
| `telegram/telegram_formatter.py` | 68 | medium | `_edit_trace_with_text` accepts `trace_obj: Any` | Narrow to `Message` |
| `telegram/telegram_formatter.py` | 84 | medium | `edit_reasoning` accepts `trace_obj: Any` | Same as above |
| `telegram/telegram_formatter.py` | 130 | medium | `edit_tool_recap` accepts `trace_obj: Any` | Same as above |
| `telegram/telegram_normalize.py` | 26 | low | `_extract_attachments` accepts `msg: Any` | Use `aiogram.types.Message` or Protocol |
| `telegram/telegram_normalize.py` | 122 | low | `normalize` accepts `raw: Any` | Same as above |
| `telegram/telegram_normalize.py` | 220 | low | `normalize_audio` accepts `raw: Any` | Same as above |
| `telegram/telegram_inbound.py` | 39 | low | `handle_message` accepts `msg: Any` | Use `aiogram.types.Message` |
| `telegram/telegram_inbound.py` | 87 | low | `handle_voice_message` accepts `msg: Any` | Same as above |
| `discord/adapter.py` | 114 | low | `self._bot_user: Any = None` | Use `discord.User \| None` |
| `discord/adapter.py` | 125 | low | `self._resolve_identity_fn: Any = None` | Define `IdentityResolver = Callable[[str, str, str], Identity]` |
| `discord/adapter.py` | 190 | low | `_handle_voice_command` accepts `message: Any` | Use `discord.Message` |
| `discord/adapter.py` | 197 | low | `normalize_audio` accepts `raw: Any` | Use `discord.Message` |
| `discord/adapter.py` | 214 | low | `normalize` accepts `raw: Any` | Same as above |
| `discord/adapter.py` | 231 | low | `on_message` accepts `message: Any` | Same as above |
| `discord/adapter.py` | 337 | low | `_resolve_channel` uses `cast(discord.abc.Messageable, channel)` | Safe cast; no change needed |
| `discord/discord_outbound.py` | 69 | low | `resolve_channel` typed as `Callable[..., Any]` | Narrow to `Callable[[int], Awaitable[discord.abc.Messageable]]` |
| `discord/discord_outbound.py` | 159 | low | `cast(_PartialMessageable, messageable)` | Safe narrowing cast |
| `discord/discord_outbound.py` | 245 | low | `cast(_PartialMessageable, messageable)` | Safe narrowing cast |
| `discord/discord_outbound.py` | 260 | medium | `_send_trace_placeholder` returns `tuple[Any, int \| None]` | Replace `Any` with `discord.Message` |
| `discord/discord_outbound.py` | 297 | medium | `_edit_trace_with_text` accepts `trace_obj: Any` | Same as above |
| `discord/discord_outbound.py` | 305 | medium | `_edit_tool_recap` accepts `trace_obj: Any` | Same as above |
| `discord/discord_outbound.py` | 322 | medium | `_render_reasoning` accepts `trace_obj: Any` | Same as above |
| `discord/discord_formatter.py` | 60 | medium | `render_buttons` accepts `buttons: Any` | Use `list[Button]` |
| `discord/discord_formatter.py` | 68 | medium | `edit_reasoning` accepts `trace_obj: Any` | Use `discord.Message` |
| `discord/discord_formatter.py` | 121 | medium | `edit_tool_recap` accepts `trace_obj: Any` | Same as above |
| `discord/discord_inbound.py` | 73 | low | `_try_auto_create_thread` accepts `raw_message: Any` | Use `discord.Message` |
| `discord/discord_inbound.py` | 141 | low | `_claim_existing_thread` accepts `raw_message: Any` | Same as above |
| `discord/discord_inbound.py` | 165 | low | `_discord_pre_session_hook` accepts `raw_message: Any` | Same as above |
| `discord/discord_inbound.py` | 213 | low | `handle_message` accepts `message: Any` | Same as above |
| `discord/discord_normalize.py` | 28 | low | `normalize` accepts `raw: Any` | Use `discord.Message` |
| `discord/discord_audio.py` | 62 | low | `normalize_audio` accepts `raw: Any` | Same as above |
| `discord/discord_audio.py` | 130 | low | `handle_audio` accepts `message: Any` and `audio_attachment: Any` | Use `discord.Message` and `discord.Attachment` |
| `discord/voice/discord_voice_commands.py` | 48 | low | `reply_safe` accepts `message: Any` | Use `discord.Message` |
| `discord/voice/discord_voice_commands.py` | 62 | low | `handle_leave_command` accepts `message: Any` | Same as above |
| `discord/voice/discord_voice_commands.py` | 79 | low | `handle_join_command` accepts `message: Any` and `guild: Any` | Use `discord.Message` and `discord.Guild` |
| `discord/voice/discord_voice_commands.py` | 115 | low | `handle_voice_command` accepts `message: Any` | Same as above |
| `discord/voice/discord_voice_commands.py` | 164 | low | `_handle_join_slash` accepts `interaction: Any` | Use `discord.Interaction` |
| `discord/voice/discord_voice_commands.py` | 221 | low | `register_voice_app_commands` accepts `CommandTree[Any]` | Use `CommandTree` concrete type or `CommandTree[discord.Client]` |
| `clipool/clipool_worker.py` | 78 | low | `_make_chunk` uses `**kwargs: Any` | Narrow to `**kwargs: str \| int \| bool \| None` via `CliChunkEvent` fields |
| `clipool/clipool_worker.py` | 93 | low | `_make_ack` uses `**kwargs: Any` | Same as above |
| `clipool/clipool_worker.py` | 148 | medium | `handle` accepts `msg: Any` | Use `nats.aio.msg.Msg` |
| `clipool/clipool_worker.py` | 163 | medium | `_handle_cmd` accepts `msg: Any` | Same as above |
| `clipool/clipool_worker.py` | 203 | medium | `_handle_cmd_streaming` accepts `msg: Any` | Same as above |
| `clipool/clipool_worker.py` | 281 | medium | `_handle_cmd_blocking` accepts `msg: Any` | Same as above |
| `clipool/clipool_worker.py` | 322 | medium | `_handle_control` accepts `msg: Any` | Same as above |
| `nats/nats_outbound_listener.py` | 65 | low | `self._sub: Any = None` | Use `nats.aio.subscription.Subscription \| None` |
| `nats/nats_outbound_listener.py` | 108 | medium | `_handle` accepts `msg: Any` | Use `nats.aio.msg.Msg` |
| `nats/nats_stream_decoder.py` | 38 | low | `health_check_fn` uses `Callable[[], Coroutine[Any, Any, bool]]` | `Any, Any` in `Coroutine` are acceptable; no change needed |
| `nats/nats_stream_decoder.py` | 156 | low | `remember_terminated` accepts `listener: Any` | Use `NatsOutboundListener` (import under `TYPE_CHECKING`) |
| `nats/nats_stream_decoder.py` | 172 | low | `reap_tombstones` accepts `listener: Any` | Same as above |
| `nats/nats_stream_decoder.py` | 184 | low | `run_reaper_loop` accepts `listener: Any` | Same as above |
| `nats/nats_stream_decoder.py` | 204 | low | `handle_stream_error` accepts `listener: Any` | Same as above |
| `nats/mint_failure_subscriber.py` | 39 | low | `nc: Any` in `__init__` | Use `nats.aio.client.Client` under `TYPE_CHECKING` |
| `nats/mint_failure_subscriber.py` | 48 | low | `self._sub: Any = None` | Use `nats.aio.subscription.Subscription \| None` |
| `nats/mint_failure_subscriber.py` | 67 | medium | `_handle` accepts `msg: Any` | Use `nats.aio.msg.Msg` |
| `shared/_shared.py` | 76 | low | `inbound_bus: Bus[Any]` | `Any` inside generic is acceptable; no change needed |
| `shared/_shared.py` | 178 | low | `coro_factory: Callable[[], Coroutine[Any, Any, None]]` | Same as above |
| `shared/_shared.py` | 220 | low | `coro_fn: Callable[[], Any]` | Narrow to `Callable[[], Awaitable[None]]` |
| `telegram/telegram_formatting.py` | 33 | low | `# type: ignore[import-untyped]` for `telegramify_markdown` | Documented as DEBT; acceptable defensive suppression |
| `telegram/telegram_formatting.py` | 37 | low | `cast(_ConvertFn, _md)` | Safe cast for conditional import; no change needed |

---

## Metrics

| Metric | Count | Note |
|---|---|---|
| Total files | 35 | — |
| Total lines | ~6,814 | — |
| Total functions | 273 | — |
| Public functions | 153 | — |
| Public functions missing return type | **0** | 100 % coverage |
| `Any` parameter annotations | ~45 | Dominantly `msg: Any`, `raw: Any`, `trace_obj: Any` |
| `Any` return annotations | ~4 | `tuple[Any, ...]`, `Any` properties |
| `Any` variable annotations | ~3 | `_bot: Any`, `_dp: Any`, `_sub: Any` |
| `Any` in generic params | ~10 | `dict[str, Any]`, `Callable[..., Any]`, `Coroutine[Any, Any, bool]`, `CommandTree[Any]` |
| `# type: ignore` | **1** | `telegram_formatting.py:33` — `import-untyped`, documented |
| `cast()` | **4** | All safe narrowing casts |
| `assert isinstance()` | **0** | — |
| Untyped `*args` / `**kwargs` | **0** | All variadics carry `Any` annotations |

**Any density:** ~52 standalone `Any` annotations across 35 files ≈ **1.5 per file**.

---

## Recommendations (prioritized)

1. **Add aiogram / discord.py / nats-py stubs or Protocols for the six "Any clusters"**
   The highest-impact, lowest-effort fix is to define local Protocols (or `TYPE_CHECKING` imports) for the five repeated shapes: `msg: Any` (aiogram `Message` / nats `Msg`), `raw: Any` (platform message), `trace_obj: Any` (placeholder `Message`), `bot: Any` / `bot: Any` (aiogram `Bot`), `interaction: Any` (discord `Interaction`). This would eliminate ~35 of the ~45 parameter-level `Any`s in a single sweep per cluster.

2. **Replace `tuple[Any, int]` returns with concrete message types**
   In `telegram_outbound.py` and `discord_outbound.py`, the streaming callbacks return `tuple[Any, int]` for placeholder objects. Narrowing to `aiogram.types.Message` / `discord.Message` would remove 4 medium-severity `Any`s and improve downstream callback contracts.

3. **Type `_make_chunk` / `_make_ack` kwargs via Pydantic field types**
   `clipool_worker.py` spreads `**kwargs: Any` into `CliChunkEvent` and `CliControlAck`. Replacing `Any` with `str | int | bool | None` (or a `TypedDict`) removes the only two `Any`-typed kwargs in the partition without adding boilerplate.

4. **Convert `dict[str, Any]` status returns to dataclasses**
   `telegram.py`'s `handle_update` and `get_status` return `dict[str, Any]`. Small inline `TypedDict` or dataclass definitions would remove these generic-level `Any`s and make FastAPI response schemas self-documenting.

5. **Schedule a follow-up stub sweep once third-party stubs mature**
   aiogram v3 and nats-py both have incomplete stub coverage. Track upstream stub releases; when available, bulk-replace the local `Any` annotations with the real types. Do not invest in custom stubs unless the project grows beyond adapter boundaries into deep SDK usage.
