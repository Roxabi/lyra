# Code Smells Analysis: Adapters + Bootstrap Tests

### Summary
The adapter and bootstrap test suites are generally well-structured with good test isolation, but exhibit several code smells including significant duplication in mock/fixture setup patterns, several functions exceeding 50 lines, and some classes with high method counts. The most critical issues are concentrated in `conftest.py` and several test files with complex async test patterns.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| tests/adapters/conftest.py | N/A | God class: 22 functions/helpers | Medium | Extract into separate fixture modules (e.g., `discord_fixtures.py`, `telegram_fixtures.py`) |
| tests/adapters/test_nats_outbound_listener.py | 1-1217 | File > 1000 lines, class with 40+ test methods | High | Split into focused test classes by envelope type (send, stream, attachment, version) |
| tests/adapters/test_streaming.py | N/A | God class: TestTelegramStreaming (20 methods), TestDiscordStreaming (13 methods) | Medium | Extract streaming tests into separate files per platform |
| tests/adapters/test_streaming_session.py | N/A | 498 lines, many similar test patterns | Medium | Use parameterized tests or pytest fixtures to reduce duplication |
| tests/adapters/test_discord_voice_commands.py | 62-341 | Deep nesting (4-5 levels in test classes) | Medium | Extract helper methods for adapter/msg construction |
| tests/adapters/test_telegram_voice.py | 23-48 | Function `_make_voice_msg` has 9 parameters | High | Use builder pattern or dataclass for message construction |
| tests/adapters/test_telegram_attachments.py | 26-51 | Function `_make_msg` has 8 parameters | High | Use builder pattern or factory fixture |
| tests/adapters/test_discord_outbound.py | N/A | DRY violation: Mock channel setup repeated 15+ times | Medium | Extract mock channel setup into reusable fixture |
| tests/adapters/test_discord_auth.py | 62-129 | DRY violation: `_make_discord_msg_ns` duplicated pattern | Low | Consolidate with conftest fixture |
| tests/adapters/test_streaming.py | 286-682 | Deep nesting (4-5 levels in test methods) | Medium | Extract assertions into helper methods |
| tests/bootstrap/test_stt_adapter_standalone.py | 109-186 | Deep nesting (4+ levels) in `_make_adapter` patterns | Low | Use pytest fixtures for adapter construction |
| tests/bootstrap/test_tts_adapter_standalone.py | 109-176 | Similar nesting patterns to STT tests | Low | Create shared base test class |
| tests/bootstrap/test_watchdog.py | N/A | DRY violation: Similar `_hang_forever` function repeated 5 times | Medium | Extract to shared fixture or helper |

### Metrics
- **Avg function length**: ~22 lines (acceptable)
- **Max function length**: 67 lines (`test_remember_terminated_evicts_oldest_first` in test_nats_outbound_listener.py)
- **God classes**: 4 (TestTelegramStreaming, TestDiscordStreaming, TestNatsOutboundListener, conftest module)
- **Duplication hotspots**: 6 (mock setup, message builders, async event helpers, watchdog tasks)

### Detailed Analysis

**Long Parameter Lists (>5 params):**
1. `_make_voice_msg` in test_telegram_voice.py - 9 params
2. `_make_msg` in test_telegram_attachments.py - 8 params
3. `_make_discord_msg` in test_discord_audio.py - 6 params
4. `make_tg_attach_msg` in conftest.py - 6 params
5. `make_dc_attach_msg` in conftest.py - 6 params

**Deep Nesting (>4 levels):**
1. test_discord_voice_commands.py - Multiple test methods with 4-5 levels of nesting in arrange/act/assert
2. test_streaming.py - TestTelegramStreaming/TestDiscordStreaming classes with nested async contexts
3. test_nats_outbound_listener.py - Multiple tests with nested patch contexts

**Files Exceeding 300 Lines:**
1. test_nats_outbound_listener.py - 1217 lines (CRITICAL)
2. test_streaming.py - 683 lines
3. test_streaming_session.py - 498 lines
4. test_discord_voice_commands.py - 376 lines
5. test_discord_outbound.py - 421 lines
6. test_telegram_normalize_fields.py - 467 lines
7. conftest.py - 321 lines

### Recommendations

1. **Priority 1 - Split test_nats_outbound_listener.py** (High Impact)
   - Create `test_nats_outbound_send.py`, `test_nats_outbound_stream.py`, `test_nats_outbound_attachment.py`, `test_nats_outbound_version.py`
   - Each file should be < 300 lines

2. **Priority 2 - Consolidate Mock/Fixture Patterns** (Medium Impact)
   - Create `tests/adapters/fixtures/message_builders.py` with builder pattern
   - Replace 9-param `_make_voice_msg` with `VoiceMessageBuilder` class
   - Consolidate `_make_adapter` patterns across Discord tests

3. **Priority 3 - Extract Common Async Helpers** (Medium Impact)
   - Create `tests/adapters/helpers/async_helpers.py`
   - Move `_hang_forever`, `_events`, `_quick_events` to shared module
   - Reduce duplication in streaming tests

4. **Priority 4 - Reduce Nesting in Test Methods** (Low Impact)
   - Extract assertion helpers for complex validations
   - Use pytest fixtures for setup, reducing arrange block depth
   - Consider table-driven tests for similar test cases

5. **Priority 5 - Split God Test Classes** (Low Impact)
   - Split TestTelegramStreaming into TextOnly, ToolSummary, Error, Overflow test classes
   - Split TestDiscordStreaming similarly
   - Split conftest.py into `telegram_fixtures.py` and `discord_fixtures.py`
