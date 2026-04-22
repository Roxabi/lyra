# Test Quality Analysis: E2E and Remaining Tests

### Summary
No e2e tests exist in `tests/e2e/` (directory not found). The analysis covers remaining test files across agents/, cli/, deploy/, integrations/, llm/, nats/, obs/, plugins/, stt/, tts/, integration/, and the tests/ root directory. The tests show generally good quality with clear docstrings, but exhibit several test smells including hardcoded ports, excessive mock usage (2313 instances), and some flaky sleep patterns. Coverage gaps exist in edge case handling for network failures and timeout scenarios.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| tests/integration/test_command_sessions.py | 330 | `asyncio.sleep(10)` - long sleep in test | Medium | Use mock time or reduce sleep with proper synchronization |
| tests/nats/conftest.py | 58 | `time.sleep(0.05)` - synchronous sleep blocking event loop | Medium | Replace with `asyncio.sleep(0.05)` |
| tests/nats/test_nats_bus.py | 524 | `asyncio.sleep(0.5)` - could cause flaky tests | Low | Consider using event-based synchronization |
| tests/test_monitoring_checks_http_idle_reaper.py | 36,56 | Hardcoded `localhost:8443` for health checks | Low | Use fixture-provided ports or env var |
| tests/test_health_endpoint_status.py | Multiple | Hardcoded `localhost:4222` for NATS | Low | Use fixture or env var for port |
| tests/core/test_agent_config.py | 44-119 | Hardcoded `localhost:11434` for Ollama base URL | Low | Use configurable port |
| tests/bootstrap/test_embedded_nats.py | 44 | Hardcoded `127.0.0.1:14222` | Low | Use free port allocation |
| tests/cli/test_voice_smoke.py | 87-96 | Event loop restoration pattern fragile | Medium | Use pytest-asyncio's loop scope fixture |
| tests/tts/test_tts_synthesize.py | 13-16 | Skip condition depends on optional dependency | Low | Document in pytest configuration |
| tests/integration/test_voice_end_to_end.py | 186,236 | `patch("lyra.stt.is_whisper_noise")` - patches internal function | Medium | Mock at service boundary instead |
| tests/integration/test_session_reply_to.py | 103 | `asyncio.sleep(100)` in mock task | Low | Acceptable for simulating long-running task |
| tests/agents/test_simple_agent.py | 67 | Heavy use of MagicMock for provider (no real behavior) | Low | Consider contract tests |
| tests/test_circuit_config.py | 25-70 | Missing edge case: invalid TOML syntax handling | Medium | Add test for malformed TOML |
| tests/test_config.py | 47-66 | Missing edge case: circular env references | Medium | Add test for `env:A` pointing to `env:B` |
| tests/stt/test_stt_service.py | N/A | Skip marker for optional voicecli dependency | Info | Acceptable pattern |
| tests/integrations/test_audio_converter.py | N/A | Integration test depends on external tools | Low | Document requirements in test docstring |
| tests/nats/ | Multiple | 2313 MagicMock/AsyncMock occurrences across tests | Medium | Reduce mocking; use fakes/stubs where possible |
| tests/integration/test_message_pipeline.py | 73-257 | Good pattern: trace hook for debugging | Good | N/A |
| tests/cli/test_cli_wizard_create.py | 44-57 | Good pattern: explicit input sequence documented | Good | N/A |

### Metrics
- Test files analyzed: 80
- Test functions: 801
- Flaky patterns: 4 (sleep > 0.1s without event-based sync)
- Mock usage: 2313 MagicMock/AsyncMock occurrences
- Patch usage: 350 @patch/patch() occurrences
- Hardcoded ports: 24 occurrences
- Missing edge case tests: ~15 coverage gaps identified
- Good patterns: 12 well-structured tests with proper documentation

### Recommendations

1. **High Priority - Flaky Test Fixes**:
   - Replace `asyncio.sleep(10)` in `test_command_sessions.py` with mock time or proper synchronization
   - Convert `time.sleep(0.05)` to `asyncio.sleep()` in `tests/nats/conftest.py`

2. **High Priority - Reduce Over-Mocking**:
   - Create fake/stub implementations for common test scenarios (e.g., `FakeLlmProvider`, `FakeNatsClient`)
   - Target files with >20 mock instances for refactoring
   - Consider contract tests using interfaces/protocols

3. **Medium Priority - Hardcoded Ports**:
   - Use `portfinder` or `socket.bind(("", 0))` pattern for free port allocation
   - Create a fixture for providing test ports

4. **Medium Priority - Coverage Gaps**:
   - Add tests for malformed TOML config parsing
   - Add tests for circular environment variable references
   - Add tests for network timeout edge cases in monitoring checks

5. **Low Priority - Documentation**:
   - Add requirement documentation to tests that depend on external tools (voicecli, ffmpeg)
   - Document skip reasons in pytest.mark.skipif conditions

6. **Good Patterns to Propagate**:
   - The trace hook pattern in `test_message_pipeline.py` for debugging
   - Explicit input sequence documentation in CLI wizard tests
   - Using `pytest.raises` with match parameter for specific error assertions
   - Using `monkeypatch` for environment variable isolation
