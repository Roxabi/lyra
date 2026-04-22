# Type Safety Analysis: Adapters

### Summary
The adapters area has moderate type safety with most public APIs properly typed, but exhibits significant use of `Any` types for platform SDK objects (discord.py, aiogram). The codebase correctly uses modern Python type hint syntax (`X | None` instead of `Optional[X]`), but lacks precise typing for external library objects, leading to 64 `Any` usages and 3 `type: ignore` suppressions.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| `/home/mickael/projects/lyra/src/lyra/adapters/discord/adapter.py` | 95 | `self._bot_user: Any = None` - Bot user object untyped | Medium | Type as `discord.ClientUser | None` |
| `/home/mickael/projects/lyra/src/lyra/adapters/discord/adapter.py` | 106 | `self._resolve_identity_fn: Any = None` - Callable untyped | Medium | Type as `Callable[[str], Awaitable[Identity | None]] | None` |
| `/home/mickael/projects/lyra/src/lyra/adapters/discord/adapter.py` | 175 | `message: Any` in `_handle_voice_command` | Medium | Type as `discord.Message` |
| `/home/mickael/projects/lyra/src/lyra/adapters/discord/adapter.py` | 182, 199 | `raw: Any` in `normalize_audio`, `normalize` | Medium | Type as `discord.Message` |
| `/home/mickael/projects/lyra/src/lyra/adapters/discord/adapter.py` | 216 | `message: Any` in `on_message` | Medium | Type as `discord.Message` |
| `/home/mickael/projects/lyra/src/lyra/adapters/discord/discord_inbound.py` | 29 | `message: Any` in `handle_message` | Medium | Type as `discord.Message` |
| `/home/mickael/projects/lyra/src/lyra/adapters/discord/discord_inbound.py` | 261 | `source_message: Any = None` | Medium | Type as `discord.Message | None` |
| `/home/mickael/projects/lyra/src/lyra/adapters/discord/discord_normalize.py` | 27 | `raw: Any` in `normalize` | Medium | Type as `discord.Message` |
| `/home/mickael/projects/lyra/src/lyra/adapters/discord/discord_audio.py` | 59, 118 | `raw: Any`, `message: Any`, `audio_attachment: Any` | Medium | Type with discord types |
| `/home/mickael/projects/lyra/src/lyra/adapters/discord/discord_formatting.py` | 62, 96 | `list[Any]` for attachments and buttons | Low | Type as `list[discord.Attachment]`, `list[Button]` |
| `/home/mickael/projects/lyra/src/lyra/adapters/discord/discord_outbound.py` | 35 | `resolve_channel: Callable[..., Any]` | Low | Type precisely |
| `/home/mickael/projects/lyra/src/lyra/adapters/discord/discord_outbound.py` | 119, 197 | `# type: ignore[attr-defined]` for `get_partial_message` | Low | Create typed wrapper or use cast |
| `/home/mickael/projects/lyra/src/lyra/adapters/discord/voice/discord_voice_commands.py` | 48, 62, 79-80, 115, 164 | Multiple `message: Any`, `guild: Any`, `interaction: Any` | Medium | Type as discord types |
| `/home/mickael/projects/lyra/src/lyra/adapters/telegram/telegram.py` | 74 | `_make_verifier` missing return type hint | Low | Add `-> Callable[[Request], Awaitable[None]]` |
| `/home/mickael/projects/lyra/src/lyra/adapters/telegram/telegram.py` | 129-130 | `self._bot: Any`, `self._dp: Any` | Medium | Type as `Bot | None`, `Dispatcher | None` |
| `/home/mickael/projects/lyra/src/lyra/adapters/telegram/telegram.py` | 144, 153, 167 | `bot` and `dp` properties return `Any` | Medium | Type as `Bot`, `Dispatcher` |
| `/home/mickael/projects/lyra/src/lyra/adapters/telegram/telegram.py` | 232, 235, 238, 243, 251 | Multiple `Any` params for aiogram types | Medium | Type as aiogram types |
| `/home/mickael/projects/lyra/src/lyra/adapters/telegram/telegram_inbound.py` | 58, 121 | `msg: Any` in handlers | Medium | Type as `aiogram.types.Message` |
| `/home/mickael/projects/lyra/src/lyra/adapters/telegram/telegram_normalize.py` | 24, 129, 207 | `raw: Any`, `msg: Any` | Medium | Type as `aiogram.types.Message` |
| `/home/mickael/projects/lyra/src/lyra/adapters/telegram/telegram_outbound.py` | 43, 72, 207, 220 | `bot: Any`, `ph: Any`, `event: Any` | Medium | Type with aiogram types |
| `/home/mickael/projects/lyra/src/lyra/adapters/telegram/telegram_formatting.py` | 33 | `# type: ignore[import-untyped]` for telegramify_markdown | Low | Consider stubs or vendor types |
| `/home/mickael/projects/lyra/src/lyra/adapters/nats/nats_stream_decoder.py` | 100, 116, 128, 148 | `listener: Any` in multiple functions | Medium | Create protocol or type as `NatsOutboundListener` |
| `/home/mickael/projects/lyra/src/lyra/adapters/nats/nats_outbound_listener.py` | 63, 106 | `self._sub: Any`, `msg: Any` | Medium | Type as `nats.aio.subscription.Subscription | None` |
| `/home/mickael/projects/lyra/src/lyra/adapters/shared/_shared.py` | 78, 222 | `Bus[Any]`, `Callable[[], Any]` | Low | Acceptable for generic bus/coro types |
| `/home/mickael/projects/lyra/src/lyra/adapters/shared/_shared_streaming_emitter.py` | 117, 193, 212 | `placeholder_obj: Any` | Medium | Consider generic type parameter |

### Metrics
- **Total lines of code**: 5,090
- **Total Python files**: 39
- **`Any` usage**: 64 instances (primarily for discord.py/aiogram types)
- **`type: ignore`**: 3 instances
- **Missing return type hints**: 1 instance (`_make_verifier`)
- **Type coverage estimate**: ~85% (most public methods have return types, but params often use `Any`)

### Recommendations

1. **High Priority**: Create type stubs or use TYPE_CHECKING imports for discord.py and aiogram types. These libraries have types available and can be imported under TYPE_CHECKING blocks.

2. **High Priority**: Replace `listener: Any` with proper type annotations in nats_stream_decoder.py - either use forward reference `"NatsOutboundListener"` or create a Protocol.

3. **Medium Priority**: Add precise type hints for message handlers in discord_inbound.py and telegram_inbound.py - these are core entry points and should be well-typed.

4. **Medium Priority**: Type the adapter facade properties (`bot`, `dp`) with their actual types rather than `Any`.

5. **Low Priority**: Address the 3 `type: ignore` comments by creating typed wrapper functions or using `cast()`.

6. **Low Priority**: Consider making `PlatformCallbacks` generic over the placeholder object type to eliminate `placeholder_obj: Any`.
