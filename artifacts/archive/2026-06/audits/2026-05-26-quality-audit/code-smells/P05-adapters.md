# Code Smells Audit — P05 Adapters (2026-05-26)

**Scope:** `src/lyra/adapters/**/*.py` (38 files, 6 814 lines)
**Context:** Epic #1277 stage-axis refactor active; prior audit on 2026-05-18 covered hexagonal conformance, mutualization/duplication, and dead-code.

---

## Summary

- **2 functions >100 lines in non-exempted files** (`discord_audio.handle_audio` 133 lines, `telegram_inbound.handle_voice_message` 108 lines) with inline guard/reply pipelines that should be extracted.
- **Reasoning state machine is copy-pasted 4×** — old `build_streaming_callbacks` closures in both Telegram and Discord outbound retain the same 3-branch accumulator/throttle logic that now lives in the new `TelegramFormatter` / `DiscordFormatter`, creating transition-period duplication.
- **Adapter god classes regressed** since prior audit: `TelegramAdapter` grew +45 lines (#1376 typing plane, #1396 factory extraction) and `DiscordAdapter` grew +44 lines, now ~10 responsibilities each.

---

## Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `src/lyra/adapters/discord/discord_audio.py` | 128 | High | `handle_audio` = 133 lines, not exempted. `noqa:C901` for complexity. Inline 5-phase pipeline (size guard → download → magic check → permission gate → hub push) with repeated try/except reply blocks. | Extract `_reply_audio_guard` helper and `_check_audio_permission` gate; split phases into named helpers. |
| `src/lyra/adapters/telegram/telegram_inbound.py` | 87 | High | `handle_voice_message` = 108 lines, not exempted. Monolithic: download, size validation, 2 error-reply paths, temp file cleanup, normalize, hub push. | Extract `_send_audio_error_reply` helper; split into `_download_voice` + `_dispatch_voice_to_hub`. |
| `src/lyra/adapters/telegram/telegram_normalize.py` | 120 | Medium | `normalize` = 95 lines, `noqa:C901` (complexity >15). File not exempted. Complex entity-loop mention detection + bot-suffix stripping with nested breaks. | Extract `_strip_bot_suffixes(text, entities, bot_suffix)` and `_detect_mention` helpers. |
| `src/lyra/adapters/discord/discord_formatter.py` | 66 | Medium | `edit_reasoning` has `noqa:C901` in non-exempted file. 3-branch state machine (accum → truncate → throttle → edit) duplicated in `discord_outbound._render_reasoning` (legacy path). | Extract shared `ReasoningStateMachine` to `adapters.shared`; compose into both formatters and delete legacy closures in S7. |
| `src/lyra/adapters/telegram/telegram_formatter.py` | 82 | Medium | `edit_reasoning` mirrors the same 3-branch state machine from `telegram_outbound._render_reasoning` — duplication introduced during S4→S7 formatter extraction. | Use shared `ReasoningStateMachine` (same fix as Discord). |
| `src/lyra/adapters/telegram/telegram.py` | 81 | Medium | `TelegramAdapter` god class (~10 responsibilities: webhook, identity, typing, send, streaming, audio, attachment, voice stream, localization, emitter). Grew +45 lines since prior audit. | Continue S7 decomposition: delete `_make_streaming_callbacks` path, move `_make_emitter` body to formatter module. |
| `src/lyra/adapters/discord/adapter.py` | 74 | Medium | `DiscordAdapter` god class (~10 responsibilities). Inherits `discord.Client` + `OutboundAdapterBase`. Grew +44 lines since prior audit. | Continue S7 decomposition; extract thread lifecycle to dedicated coordinator. |
| `src/lyra/adapters/telegram/telegram_outbound.py` | 187 | Low | `build_streaming_callbacks` = ~200 lines in exempted file. Legacy path retained during S4→S7 transition. Contains reasoning/tool-recap closures already superseded by `TelegramFormatter`. | Complete S7 cleanup per exemption comment — delete `build_streaming_callbacks` and shrink file below 200 lines. |
| `src/lyra/adapters/discord/discord_outbound.py` | 193 | Low | `build_streaming_callbacks` = ~190 lines in exempted file. Mirrors Telegram pattern; legacy path duplicates `DiscordFormatter` reasoning logic. | Complete S7 cleanup per exemption comment. |
| `src/lyra/adapters/telegram/telegram_outbound.py` | 39 | Low | `TelegramTypingIndicator` and `DiscordTypingIndicator` (discord_outbound.py:48) are structurally identical — introduced in #1376. | Unify to generic `AdapterTypingIndicator` in `adapters.shared`. |
| `src/lyra/adapters/nats/nats_envelope_handlers.py` | 36 | Low | `handle_send` / `handle_attachment` / `handle_audio` share identical 5-step pipeline (resolve → version-check → deserialize → dispatch → cache-pop) with only the Pydantic class differing. | Extract generic `_dispatch_outbound_envelope(listener, data, kind, pydantic_cls)`. |

---

## Metrics

| Metric | Value | Notes |
|--------|-------|-------|
| Total files | 38 | `src/lyra/adapters/**/*.py` |
| Total lines | 6 814 | |
| Files >300 lines | 5 / 38 (13%) | All have active exemptions (file_exemptions.txt) |
| Files 100–300 lines | 22 / 38 (58%) | Non-exempted |
| Files <100 lines | 11 / 38 (29%) | Thin delegates, `__init__`, re-exports |
| Functions >100 lines (non-exempted) | **2** | `discord_audio.handle_audio` (133), `telegram_inbound.handle_voice_message` (108) |
| God classes (>5 responsibilities) | **2** | `TelegramAdapter`, `DiscordAdapter` |
| `noqa:C901` markers (complexity >15) | 7 total | 3 in non-exempted files (discord_audio, telegram_normalize, discord_formatter); 4 in exempted files |
| DRY violation clusters | **5** | (1) reasoning state machine 4×, (2) typing indicator 2×, (3) build_streaming_callbacks pattern 2×, (4) NATS envelope handlers 3×, (5) _validate_inbound 2× |

---

## Recommendations (prioritized)

1. **Extract shared `ReasoningStateMachine`** — Eliminates the 4× copy-paste of the 3-branch accumulator/throttle/truncate logic across `telegram_outbound`, `discord_outbound`, `TelegramFormatter`, and `DiscordFormatter`. A single class holding `_reasoning_accum`, `_last_reasoning_edit`, and an `apply(event, edit_fn)` method would cut ~60 lines from each of the 4 locations.

2. **Split non-exempted >100-line functions** — `discord_audio.handle_audio` (133 lines) and `telegram_inbound.handle_voice_message` (108 lines) both inline 3+ try/except guard/reply blocks. Extract `_send_guarded_reply(adapter, message, i18n_key, label)` and platform-specific permission gates to bring both under 80 lines.

3. **Complete S7 transition and delete `build_streaming_callbacks`** — Both `telegram_outbound.py` and `discord_outbound.py` are kept above 300 lines solely because the legacy ~200-line closure factories are still present. The `_make_emitter` + `Formatter` path is already active. Deleting the legacy functions would drop both files below 200 lines and remove 2 of the 4 reasoning-duplication sites.

4. **Unify identical `TypingIndicator` classes** — `TelegramTypingIndicator` and `DiscordTypingIndicator` differ only by module path. A single `AdapterTypingIndicator(adapter)` class in `adapters.shared` removes 2× duplication and simplifies future platform additions.

5. **Generic NATS outbound envelope handler** — `handle_send`, `handle_attachment`, and `handle_audio` are 15-line templates with only the Pydantic type differing. A single `_dispatch_typed_outbound(listener, data, envelope_name, expected_schema, pydantic_cls)` helper would collapse the 3 handlers to ~5 lines each, improving maintainability as new envelope types are added.
