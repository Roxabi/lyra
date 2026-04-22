# Test Quality Analysis: Coverage Analysis

### Summary
The Lyra project has 261 test files with 2807 test functions, but overall line coverage is only 52.51% with branch coverage at 37.99%. Critical gaps exist in adapters (discord_outbound: 12%, telegram_outbound: 12.9%, discord_audio: 15.19%), bootstrap modules (unified: 19.84%), and agents (simple_agent: 0%, anthropic_agent: 0%). Over-mocking is prevalent in 13 test files with 25+ mock occurrences, and 13 tests use long sleep durations (>1s) indicating potential flakiness.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| `/home/mickael/projects/lyra/src/lyra/agents/simple_agent.py` | 0% | Zero coverage on production agent | High | Add integration tests for SimpleAgent |
| `/home/mickael/projects/lyra/src/lyra/agents/anthropic_agent.py` | 0% | Zero coverage on Anthropic agent | High | Add unit tests for anthropic integration |
| `/home/mickael/projects/lyra/src/lyra/adapters/discord_outbound.py` | 12% | Critical adapter only 12% covered | High | Expand test coverage for Discord outbound |
| `/home/mickael/projects/lyra/src/lyra/adapters/telegram_outbound.py` | 12.9% | Critical adapter only 12.9% covered | High | Expand test coverage for Telegram outbound |
| `/home/mickael/projects/lyra/src/lyra/adapters/discord_audio.py` | 15.19% | Audio handling poorly tested | Medium | Add audio pipeline tests |
| `/home/mickael/projects/lyra/src/lyra/bootstrap/unified.py` | 19.84% | Bootstrap core poorly covered | High | Add bootstrap integration tests |
| `/home/mickael/projects/lyra/tests/cli/test_voice_smoke.py` | - | 61 mock occurrences | Medium | Refactor to reduce mocking, use real components |
| `/home/mickael/projects/lyra/tests/adapters/test_discord_outbound.py` | - | 58 mock occurrences | Medium | Consider contract tests instead |
| `/home/mickael/projects/lyra/tests/adapters/test_discord_voice_commands.py` | - | 54 mock occurrences | Medium | Split into focused test files |
| `/home/mickael/projects/lyra/tests/conftest.py` | 88, 110 | `asyncio.sleep(1_000)` - 16+ minute sleeps | High | Replace with event-based synchronization |
| `/home/mickael/projects/lyra/tests/core/hub/test_message_pipeline_stt.py` | 58 | `asyncio.sleep(9999)` - extreme wait | High | Use mock time or proper timeout testing |
| `/home/mickael/projects/lyra/tests/core/conftest_cli_pool.py` | 41 | `asyncio.sleep(3600)` - 1 hour sleep | High | Use cancellation testing patterns |
| `/home/mickael/projects/lyra/tests/conftest.py` | 128 | Hardcoded `localhost:4222` NATS URL | Low | Use configurable test ports |
| `/home/mickael/projects/lyra/src/lyra/cli.py` | 0% | CLI entrypoint untested | Medium | Add CLI smoke tests |
| `/home/mickael/projects/lyra/src/lyra/bootstrap/embedded_nats.py` | 19.57% | Embedded NATS poorly covered | Medium | Add embedded NATS lifecycle tests |
| `/home/mickael/projects/lyra/src/lyra/adapters/_shared_text.py` | 17.78% | Shared text handling gap | Medium | Add shared module tests |
| Multiple integration tests | Various | `pass` statements in exception handlers | Low | Add assertions for exception cases |
| `/home/mickael/projects/lyra/packages/roxabi-nats/src/roxabi_nats/circuit_breaker.py` | 0% | Package circuit breaker untested | Medium | Port tests from main circuit_breaker |

### Metrics

- **Test files**: 261
- **Test functions**: 2807
- **Overall line coverage**: 52.51%
- **Branch coverage**: 37.99%
- **Source classes <80% coverage**: 196 (out of 232)
- **Source classes <40% coverage**: 45
- **Source classes 0% coverage**: 20+
- **Flaky patterns (long sleeps)**: 13 occurrences
- **Mock usage (total)**: 1619 across 142 files
- **Files with over-mocking (>25 mocks)**: 13
- **Files with hardcoded ports**: 23 occurrences
- **Try/except blocks**: 397 across 96 files

### Recommendations

1. **PRIORITY 1 - Coverage Gaps on Critical Paths**:
   - Add integration tests for `simple_agent.py` and `anthropic_agent.py` (0% coverage)
   - Expand adapter outbound tests (discord_outbound: 12%, telegram_outbound: 12.9%)
   - Add bootstrap integration tests for `unified.py` (19.84%)

2. **PRIORITY 2 - Flaky Test Patterns**:
   - Replace `asyncio.sleep(1_000)` and other extreme sleeps in conftest.py with `asyncio.Event` or `asyncio.Queue` synchronization
   - Fix `asyncio.sleep(9999)` in message pipeline tests - use proper timeout mocking
   - Refactor tests using `asyncio.sleep(3600)` to use task cancellation patterns

3. **PRIORITY 3 - Over-mocking Reduction**:
   - Refactor `test_voice_smoke.py` (61 mocks) to use real NATS client with embedded server
   - Consider contract/integration tests for `test_discord_outbound.py` (58 mocks)
   - Split large mock-heavy test files into focused unit tests

4. **PRIORITY 4 - Missing Edge Case Tests**:
   - Add tests for CLI entrypoints (`cli.py`, `cli_bot.py`, `cli_setup.py` - 0% coverage)
   - Add embedded NATS lifecycle tests (startup, shutdown, error handling)
   - Add tests for shared audio/text modules

5. **PRIORITY 5 - Test Hygiene**:
   - Add assertion messages to tests in files with `pass` statements in exception handlers
   - Replace hardcoded `localhost:4222` with configurable test fixtures
   - Review 397 try/except blocks - ensure exceptions are properly asserted
