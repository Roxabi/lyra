# Code Smells Analysis: Core Unit Tests

### Summary
The core unit test suite comprises 116 test files with 1,458 test functions totaling approximately 27,000 lines. The test code is generally well-structured but exhibits several code smells: over-reliance on `asyncio.sleep()` for timing-dependent tests (67 occurrences), extensive mock usage (665 instances), and several oversized test files exceeding 500 lines. A few test classes approach god-class territory with 10+ methods, and some tests use `pass` statements or lack meaningful assertions.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| /home/mickael/projects/lyra/tests/core/conftest.py | 1-905 | Oversized conftest (905 lines) | High | Split into domain-specific conftest files (auth, pool, hub, messaging) |
| /home/mickael/projects/lyra/tests/core/test_submit_middleware_context.py | 1-838 | Oversized test file (838 lines) | High | Split into TestReplyToResume, TestResolveContextMessageIndex, TestResolveContextResumeStatus, TestNotifySessionFallthrough |
| /home/mickael/projects/lyra/tests/core/test_middleware.py | 1-758 | Oversized test file (758 lines) | Medium | Split into per-middleware test files (validate_platform, rate_limit, command, etc.) |
| /home/mickael/projects/lyra/tests/core/test_stream_processor.py | 1-721 | Oversized test file (721 lines) | Medium | Split by test groups (text-only, edit-tools, bash, web-fetch, agent-calls) |
| /home/mickael/projects/lyra/tests/core/test_command_router_special.py | 1-554 | Oversized file (554 lines), deep nesting | Medium | Split into TestBangPrefixFallthrough, TestBareUrlDetection, TestSessionCommands |
| /home/mickael/projects/lyra/tests/core/test_turn_store.py | 1-532 | Oversized test file (532 lines) | Medium | Split into TestTurnStoreSchema, TestTurnStoreLogTurn, TestTurnStoreIntegration |
| /home/mickael/projects/lyra/tests/core/test_json_agent_store.py | 1-533 | Oversized test file (533 lines) | Medium | Split into CRUD tests and query tests |
| /home/mickael/projects/lyra/tests/core/test_middleware.py | 55-710 | God class: TestValidatePlatform + 14 other test classes in one file | High | Extract each class to its own file |
| /home/mickael/projects/lyra/tests/core/test_outbound_dispatcher_media.py | 51-348 | Flaky pattern: 12 asyncio.sleep calls | High | Use deterministic synchronization (events, callbacks) instead of sleep |
| /home/mickael/projects/lyra/tests/core/test_outbound_dispatcher_coverage.py | 56-401 | Flaky pattern: 10 asyncio.sleep calls | High | Use deterministic synchronization |
| /home/mickael/projects/lyra/tests/core/test_outbound_dispatcher_queue.py | 36-182 | Flaky pattern: 8 asyncio.sleep calls | Medium | Use deterministic synchronization |
| /home/mickael/projects/lyra/tests/core/test_turn_store.py | 121, 321, 431, 456 | Flaky pattern: 4 asyncio.sleep(0.01) for timestamp ordering | Low | Use explicit timestamps injection |
| /home/mickael/projects/lyra/tests/core/ (multiple files) | - | Over-mocking: 665 mock/patch instances | Medium | Prefer real implementations where feasible; reserve mocks for external dependencies |
| /home/mickael/projects/lyra/tests/core/test_hub_circuit_streaming.py | - | Missing assertions: 10 pass statements | Medium | Add meaningful assertions or remove redundant tests |
| /home/mickael/projects/lyra/tests/core/test_submit_middleware_context.py | - | Missing assertions: 15 pass statements | Medium | Add meaningful assertions |
| /home/mickael/projects/lyra/tests/core/conftest.py | 229, 823, 877 | Long parameter lists (7+ params in factory functions) | Low | Use kwargs or dataclass builder pattern |
| /home/mickael/projects/lyra/tests/core/test_command_router_special.py | 125-169 | Deep nesting (5+ levels in hub integration test) | Medium | Extract helper functions or use pytest fixtures |
| /home/mickael/projects/lyra/tests/core/test_pool_streaming.py | 366 | Deep nesting with await inside nested context | Low | Extract to helper method |

### Metrics

- **Test files**: 116
- **Test functions**: 1,458
- **Total lines**: ~27,054
- **Flaky patterns** (sleep usage): 67 occurrences across 30+ files
- **Mock usage**: 665 occurrences across 53 files
- **Files > 500 lines**: 8 files
- **Tests with pass statements**: 81 occurrences in 20 files

### Recommendations

#### Priority 1 - High Impact
1. **Refactor conftest.py** (905 lines) into domain-specific modules:
   - `conftest_auth.py` - AuthStore, Authenticator fixtures
   - `conftest_pool.py` - Pool, PoolContext fixtures
   - `conftest_messaging.py` - Message factory functions
   - `conftest_hub.py` - Hub, adapter, binding helpers

2. **Eliminate flaky sleep patterns** in dispatcher tests:
   - Replace `asyncio.sleep()` with `asyncio.Event` or `Queue.get()` synchronization
   - Target files: test_outbound_dispatcher_media.py, test_outbound_dispatcher_coverage.py

3. **Split oversized test files** (>500 lines):
   - test_submit_middleware_context.py -> 4 files by test class
   - test_middleware.py -> 7+ files (one per middleware class)
   - test_stream_processor.py -> 5 files by functional area

#### Priority 2 - Medium Impact
4. **Reduce mock proliferation** - Identify tests that can use real implementations:
   - Use real SQLite in-memory databases instead of MagicMock for stores
   - Use real AgentBase subclasses instead of MagicMock for agents

5. **Add missing assertions** - Review 81 pass statements for:
   - Tests that should verify behavior but don't
   - Redundant test methods that can be removed

6. **Extract deep nesting** in hub integration tests:
   - Use pytest fixtures to set up hub state
   - Extract inline class definitions to module level

#### Priority 3 - Low Impact
7. **Simplify factory functions** with long parameter lists:
   - Use `@dataclass` for message builders
   - Accept kwargs with sensible defaults

8. **Standardize test organization**:
   - One test class per file for classes >200 lines
   - Group related tests by functional area
