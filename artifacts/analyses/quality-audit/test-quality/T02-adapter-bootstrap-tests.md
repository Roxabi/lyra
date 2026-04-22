# Test Quality Analysis: Adapter + Bootstrap Tests

### Summary

The adapter and bootstrap test suites are comprehensive with 523 total test functions across 49 files, but exhibit several test quality issues: moderate over-mocking (1,093 mock/patch calls across 523 tests ≈ 2.1 per test), potential flakiness from async sleep patterns, hardcoded NATS URLs, and inconsistent assertion message usage. The test coverage is strong for core paths but missing edge cases around error recovery scenarios.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| tests/adapters/test_telegram_typing.py | 49 | Flaky pattern: `asyncio.sleep(interval * 5)` relies on timing | Medium | Use deterministic synchronization or mock asyncio.sleep |
| tests/adapters/test_telegram_typing.py | 71,106 | Magic sleep values (0.12, 0.1) without clear justification | Low | Add comments explaining timing requirements or use mocks |
| tests/adapters/test_discord_typing.py | 37,76,82,102,105,234 | Multiple `asyncio.sleep(0)` yields - indicates test timing sensitivity | Medium | Consider using `asyncio.Event` for explicit synchronization |
| tests/bootstrap/test_embedded_nats.py | 133 | Long sleep `asyncio.sleep(10)` in test path | High | Mock or reduce timeout - 10s sleep is excessive for unit tests |
| tests/bootstrap/test_embedded_nats.py | 44 | Hardcoded NATS URL `nats://127.0.0.1:14222` | Low | Use fixture or environment variable for port configuration |
| tests/bootstrap/*.py | Multiple | Hardcoded `nats://localhost:4222` NATS URLs in 5 files | Medium | Extract NATS URL to shared test fixture |
| tests/adapters/test_nats_outbound_listener.py | 401,1115-1166 | Complex mock orchestration with `monkeypatch.setattr` for sleep | Medium | Consider reusable helper for async timing mocks |
| tests/adapters/conftest.py | 277-303 | Mock-heavy fixtures (inbound_bus, telegram_adapter, discord_adapter) | Low | Acceptable for unit tests but consider integration test variants |
| tests/adapters/test_streaming.py | 464-471 | Monkey-patches module-level constant `_CHUNK_TIMEOUT_SECONDS` | Medium | Consider dependency injection for timeout values |
| tests/bootstrap/test_stt_adapter_standalone.py | 36-42 | RED-phase test that checks source code for patterns | Low | Consider making this a lint rule instead of runtime test |
| tests/bootstrap/test_auth_seeding.py | 26-91 | Heavy mocking with RuntimeError sentinel for early termination | Low | Consider refactoring into smaller, focused test cases |
| tests/adapters/*.py | Multiple | Missing assertion messages on many `assert` statements | Low | Add explanatory messages: `assert x, "expected x to be truthy"` |
| tests/adapters/test_streaming_session.py | 56 | `# noqa: RET503` comment indicates incomplete generator | Low | Ensure test generators are properly structured |
| tests/adapters/test_base_outbound.py | 6 | RED-phase comment indicates tests written before implementation | Info | Remove comment once implementation exists |

### Metrics

- Test files: 49 (35 adapters + 14 bootstrap)
- Test functions: 523 (423 adapters + 100 bootstrap)
- Mock usage: 1,093 instances (853 adapters + 240 bootstrap) ≈ 2.1 per test
- Flaky patterns: 12+ locations with `asyncio.sleep` that could cause timing issues
- Hardcoded ports/URLs: 6 instances of `localhost:4222` or `127.0.0.1:14222`
- Skipped tests: 0 in target directories (skips exist in other test directories)

### Recommendations

1. **HIGH PRIORITY**: Remove or mock the 10-second sleep in `test_embedded_nats.py:133`. This significantly slows down test suite execution.

2. **HIGH PRIORITY**: Address flaky async timing patterns in typing tests. Replace `asyncio.sleep(0)` yields with explicit `asyncio.Event` synchronization for deterministic test behavior.

3. **MEDIUM PRIORITY**: Extract NATS URL configuration to a shared test fixture. Create a `nats_url` fixture in conftest.py to centralize test configuration.

4. **MEDIUM PRIORITY**: Consider dependency injection for timeout constants like `_CHUNK_TIMEOUT_SECONDS` to avoid monkey-patching module-level variables.

5. **MEDIUM PRIORITY**: Review mock-to-test ratio (2.1 mocks per test). For integration-critical paths, add tests that use real objects where feasible.

6. **LOW PRIORITY**: Add assertion messages to complex assertions. Pattern: `assert condition, "Expected X because Y"` for debugging failed tests.

7. **LOW PRIORITY**: Convert RED-phase source-code pattern tests to static analysis/lint rules where appropriate, reducing test runtime overhead.
