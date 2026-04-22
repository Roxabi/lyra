# Code Smells Analysis: Integration Tests (Second Half)

### Summary
The integration test files in the second half show generally good structure with small helper classes and reasonably sized test functions. The primary issues are code duplication (DRY violations) with the `_FakeTurnStore` mock class appearing identically in multiple files, and one long test function that exceeds 50 lines. A shared voice message factory also exists in both a central helper and a test-specific implementation.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| `/home/mickael/projects/lyra/tests/integration/test_voice_end_to_end.py` | 131-215 | Long function (82 lines): `test_voice_message_stt_populates_text_and_clears_audio` | Medium | Split into smaller focused test methods or extract setup/teardown into fixtures |
| `/home/mickael/projects/lyra/tests/integration/test_session_dm_discord.py` | 22-38 | DRY violation: `_FakeTurnStore` duplicated across 3+ files in tests/integration | High | Extract to `tests/conftest.py` or `tests/helpers/` as shared fixture |
| `/home/mickael/projects/lyra/tests/integration/test_session_telegram.py` | 21-37 | DRY violation: Identical `_FakeTurnStore` class | High | Same as above - consolidate with shared fixture |
| `/home/mickael/projects/lyra/tests/integration/test_session_reply_to.py` | 65-76 | DRY violation: `_FakeTurnStore` variant (simpler but same concept) | High | Use shared fixture with configurable behavior |
| `/home/mickael/projects/lyra/tests/integration/test_voice_end_to_end.py` | 48-77 | Duplication: `_make_voice_message` duplicates `tests/helpers/messages.py:make_voice_message` | Medium | Import from `tests.helpers` instead of redefining |
| `/home/mickael/projects/lyra/tests/integration/test_voice_end_to_end.py` | 48 | Long parameter list (6 params) in `_make_voice_message` | Low | Use builder pattern or kwargs - already acknowledged with `# noqa: PLR0913` |

### Metrics
- Avg function length: 18 lines (excluding the 82-line outlier)
- Max function length: 82 lines (`test_voice_message_stt_populates_text_and_clears_audio`)
- God classes: 0 (max methods per class is 3)
- Duplication hotspots: 2
  - `_FakeTurnStore`: Found in 12+ locations across entire test suite, 3 in scope
  - Voice message factory: Duplicated in test file vs `tests/helpers/messages.py`

### Recommendations

1. **High Priority: Extract `_FakeTurnStore` to shared fixture**
   - Create a configurable `FakeTurnStore` fixture in `tests/conftest.py` or `tests/helpers/`
   - Supports both simple (fixed session_id) and configurable (session_map) use cases
   - Affects 12+ files across the entire test suite

2. **Medium Priority: Refactor long test function**
   - `test_voice_message_stt_populates_text_and_clears_audio` (82 lines) should be split into:
     - `test_voice_message_injection_to_bus` (lines 135-166)
     - `test_stt_middleware_populates_text` (lines 168-197)
     - `test_stt_middleware_clears_audio_and_echoes` (lines 199-215)

3. **Medium Priority: Use existing helper functions**
   - Replace local `_make_voice_message` in `test_voice_end_to_end.py` with import from `tests.helpers.make_voice_message`
   - The helper already exists with the same functionality

4. **Low Priority: Address parameter count**
   - The `# noqa: PLR0913` comment indicates awareness of the 6-parameter function
   - Consider kwargs-only signature with `**overrides` pattern (already used in helper)
