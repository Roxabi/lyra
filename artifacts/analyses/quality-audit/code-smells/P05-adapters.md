# Code Smell Analysis: Adapters

### Summary
The adapters area shows a well-structured decomposition (V4 refactoring) that has successfully kept individual files under 300 lines. However, two facade classes (`DiscordAdapter` and `TelegramAdapter`) qualify as God classes with 22+ methods each, and several functions exceed 50 lines significantly, with `handle_message()` in discord_inbound.py being the worst offender at ~227 lines. DRY violations exist between Discord and Telegram adapters for validation and typing patterns.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| `/home/mickael/projects/lyra/src/lyra/adapters/discord/discord_inbound.py` | 29-256 | Function `handle_message()` ~227 lines | High | Extract into smaller focused functions (auto-thread logic, session retrieval, DM wiring) |
| `/home/mickael/projects/lyra/src/lyra/adapters/discord/discord_audio.py` | 116-237 | Function `handle_audio()` ~121 lines | High | Extract audio gate logic and thread ownership check into separate functions |
| `/home/mickael/projects/lyra/src/lyra/adapters/discord/discord_outbound.py` | 152-257 | Function `build_streaming_callbacks()` ~105 lines | Medium | Consider extracting callback builder helpers |
| `/home/mickael/projects/lyra/src/lyra/adapters/telegram/telegram_outbound.py` | 161-270 | Function `build_streaming_callbacks()` ~109 lines | Medium | Duplicate of Discord pattern - extract shared callback builder |
| `/home/mickael/projects/lyra/src/lyra/adapters/discord/discord_audio_outbound.py` | 40-114 | Function `render_audio()` ~74 lines | Medium | Extract payload construction and fallback logic |
| `/home/mickael/projects/lyra/src/lyra/adapters/telegram/telegram_inbound.py` | 121-218 | Function `handle_voice_message()` ~97 lines | Medium | Similar to Discord audio handling - unify patterns |
| `/home/mickael/projects/lyra/src/lyra/adapters/telegram/telegram_normalize.py` | 127-202 | Function `normalize()` ~75 lines | Low | Extract attachment extraction and routing construction |
| `/home/mickael/projects/lyra/src/lyra/adapters/nats/nats_stream_decoder.py` | 36-97 | Function `decode_stream_events()` ~61 lines | Low | Consider extracting sequence validation |
| `/home/mickael/projects/lyra/src/lyra/adapters/nats/nats_stream_decoder.py` | 148-213 | Function `handle_stream_error()` ~65 lines | Low | Acceptable given error handling complexity |
| `/home/mickael/projects/lyra/src/lyra/adapters/shared/_shared_streaming_emitter.py` | 114-176 | Function `_run_event_loop()` ~62 lines | Medium | Has 5 levels of nesting - extract inner branches |
| `/home/mickael/projects/lyra/src/lyra/adapters/shared/_shared.py` | 76-128 | Function `push_to_hub_guarded()` ~52 lines | Low | Acceptable given guard complexity |
| `/home/mickael/projects/lyra/src/lyra/adapters/discord/adapter.py` | 55-270 | God class `DiscordAdapter` 22 methods | High | Already well-delegated to submodules; facade is acceptable but consider further extraction |
| `/home/mickael/projects/lyra/src/lyra/adapters/telegram/telegram.py` | 85-284 | God class `TelegramAdapter` 24 methods | High | Already well-delegated; facade pattern acceptable |
| `/home/mickael/projects/lyra/src/lyra/adapters/discord/adapter.py` | 64-107 | Constructor 10+ parameters | High | Use config object pattern (like `TelegramConfig`) |
| `/home/mickael/projects/lyra/src/lyra/adapters/nats/nats_outbound_listener.py` | 40-67 | Constructor 6 parameters | Medium | Consider config object |
| `/home/mickael/projects/lyra/src/lyra/adapters/shared/_shared.py` | 76-86 | Function `push_to_hub_guarded()` 7 parameters | Medium | Use kwargs-only pattern already applied |
| `/home/mickael/projects/lyra/src/lyra/adapters/discord/discord_formatting.py` | 106-126 | Duplicate `_validate_inbound()` pattern | Low | Shared with Telegram - extract to shared module |
| `/home/mickael/projects/lyra/src/lyra/adapters/telegram/telegram_formatting.py` | 73-91 | Duplicate `_validate_inbound()` pattern | Low | Mirror of Discord implementation |
| `/home/mickael/projects/lyra/src/lyra/adapters/shared/_shared_streaming_emitter.py` | 28-48 | `PlatformCallbacks` dataclass 11 fields | Medium | Consider grouping into sub-configs |
| `/home/mickael/projects/lyra/src/lyra/adapters/shared/_shared_streaming_emitter.py` | 114-176 | Deep nesting 5 levels in `_run_event_loop()` | Medium | Extract tool event handling and text event handling |
| `/home/mickael/projects/lyra/src/lyra/adapters/discord/discord_inbound.py` | 29-256 | Deep nesting in `handle_message()` | High | Multiple 5+ level nests in auto-thread and session wiring |

### Metrics

- **Avg function length**: ~45 lines (estimated across all functions)
- **Max function length**: 227 lines (`handle_message` in discord_inbound.py)
- **God classes**: 2 (`DiscordAdapter`, `TelegramAdapter`)
- **Duplication hotspots**: 3 (validation pattern, typing worker pattern, audio handling pattern)
- **Functions > 50 lines**: 12
- **Constructors > 5 params**: 3
- **Deep nesting violations**: 2

### Recommendations

**Priority 1 - High Severity:**
1. Refactor `discord_inbound.handle_message()` into 4-5 focused functions: `_check_message_filters()`, `_handle_auto_thread()`, `_retrieve_session()`, `_inject_session_metadata()`
2. Introduce `DiscordConfig` dataclass (matching `TelegramConfig` pattern) to reduce `DiscordAdapter.__init__` parameters from 10+ to 2-3

**Priority 2 - Medium Severity:**
3. Extract `_validate_inbound()` from both platform formatting modules into `shared/_shared.py` as generic `_validate_platform_inbound()`
4. Flatten `_run_event_loop()` in `_shared_streaming_emitter.py` by extracting `_handle_tool_event()` and `_handle_text_event()` helpers
5. Create shared `build_streaming_callbacks_base()` that both Discord and Telegram can extend

**Priority 3 - Low Severity:**
6. Document the God class facades as intentional delegation pattern (they already delegate to submodules effectively)
7. Consider extracting audio gate logic (size check, format validation) into a shared `_validate_audio()` helper
8. Add `# noqa: PLR0913` comments are already present for DI constructors - maintain this convention

### Architectural Notes

The V4 decomposition (#773) has successfully achieved its goal of keeping files under 300 lines. The facade classes (`DiscordAdapter`, `TelegramAdapter`) serve as thin delegates to submodules, which is an acceptable God class pattern. The main technical debt lies in long functions rather than class structure, particularly in inbound message handling where complex routing logic accumulates.
