# Test Quality Analysis: Core Unit Tests

### Summary
The core unit tests demonstrate solid coverage with 110 test files and 1458 test functions. The test suite shows good practices including no skipped/xfail tests, proper use of fixtures, and well-organized test structure. However, there are several areas for improvement around timing-dependent tests (asyncio.sleep usage), and some tests would benefit from assertion messages for better debugging.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| /home/mickael/projects/lyra/tests/core/test_pool_manager.py | 181, 200 | asyncio.sleep(0.05) for timing-dependent tests | Medium | Use deterministic synchronization (Events/Conditions) instead of sleeps |
| /home/mickael/projects/lyra/tests/core/test_hub_circuit_streaming.py | 70, 133, 196, 278 | Multiple asyncio.sleep calls for concurrent test timing | Medium | Consider using asyncio.Event for deterministic signaling |
| /home/mickael/projects/lyra/tests/core/test_outbound_dispatcher_concurrent.py | 49, 95, 179 | Sleep-based timing for concurrent dispatch tests | Medium | Replace with asyncio.Event-based synchronization |
| /home/mickael/projects/lyra/tests/core/test_outbound_dispatcher_queue.py | 36, 51, 69, 79 | Multiple sleep(0.05) calls in queue tests | Low | Acceptable for queue drain timing, but could use queue.join() |
| /home/mickael/projects/lyra/tests/core/test_debouncer_pool.py | 142, 186 | sleep(10) and sleep(0.1) for agent timing | Low | sleep(10) is intentional for slow agent; acceptable |
| /home/mickael/projects/lyra/tests/core/test_audio_pipeline_tts.py | 296, 486 | sleep(0.1) for audio processing timing | Low | Acceptable for audio pipeline tests |
| /home/mickael/projects/lyra/tests/core/test_agent_config.py | 44, 89-119 | Hardcoded localhost URLs (http://localhost:11434/v1) | Low | Expected for local Ollama model config tests |
| /home/mickael/projects/lyra/tests/core/processors/test_scraping.py | 385, 390, 400, 411 | Localhost rejection tests (intentional) | Info | These test URL validation - localhost rejection is correct behavior |
| /home/mickael/projects/lyra/tests/core/test_circuit_breaker.py | Various | Missing assertion messages on critical state checks | Low | Add messages like `assert cb._state == CircuitState.OPEN, "Circuit should be open after 3 failures"` |
| /home/mickael/projects/lyra/tests/core/conftest.py | 727 | SlowAgent uses sleep(10) - acceptable pattern | Info | Intentional slow agent for timeout testing |
| /home/mickael/projects/lyra/tests/core/test_middleware.py | 474-527 | Extensive mocking for middleware tests | Info | Appropriate isolation for unit tests |

### Metrics
- Test files: 110
- Test functions: 1458
- Flaky patterns: 87 sleep() calls across 36 files
- Mock usage: 1526 instances across 68 files
- Hardcoded ports: 0 (localhost URLs are config tests, not ports)
- Skipped/xfail tests: 0

### Recommendations
1. **Priority High - Flaky Test Mitigation**: Replace asyncio.sleep-based timing in concurrent tests (test_outbound_dispatcher_concurrent.py, test_hub_circuit_streaming.py, test_pool_manager.py) with asyncio.Event-based deterministic synchronization. This eliminates timing-dependent flakiness.

2. **Priority Medium - Assertion Messages**: Add assertion messages to critical state checks, especially in circuit breaker tests and pool lifecycle tests. Example: `assert result is not None, "Agent should be registered after upsert"`.

3. **Priority Low - Sleep Values Audit**: Review sleep values under 50ms - some tests use 0.01s sleeps that may not provide enough buffer on slow CI runners. Consider standardizing minimum sleep at 50ms for timing-dependent tests.

4. **Info - Good Patterns Observed**:
   - Fixture-based store setup with proper teardown (auth_store, agent_store fixtures)
   - Deterministic mock patterns using AsyncMock/MagicMock appropriately
   - Well-organized test classes with clear docstrings
   - No skipped or xfail tests indicating good test reliability
   - Proper use of tmp_path fixtures for filesystem isolation
