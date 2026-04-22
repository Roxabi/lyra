# Code Smells Analysis: E2E and Remaining Tests

### Summary
No `tests/e2e` directory exists. The analysis covers remaining test files not in unit/core, unit/adapters, unit/bootstrap, or integration directories. The codebase shows generally good test organization with class-based grouping, but exhibits several code smells: one very large file (894 lines), repeated mock setup patterns across files, and helper functions with long parameter lists. Deep nesting is common in async test methods using multiple context managers.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| tests/nats/test_nats_bus.py | 1-894 | File exceeds 300 lines (894 lines) | High | Split into focused test modules: TestNatsBusLifecycle, TestNatsBusRoundTrip, TestPublishOnlyMode, TestNatsBusVersionMismatch |
| tests/cli/test_voice_smoke.py | 1-555 | File exceeds 300 lines (555 lines) | Medium | Split by test concern: happy path, failures, heartbeat validation |
| tests/stt/test_stt_service.py | 1-481 | File exceeds 300 lines (481 lines) | Medium | Split into transcribe tests, daemon state machine tests, config tests |
| tests/test_health_endpoint_status.py | 1-427 | File exceeds 300 lines (427 lines) | Medium | Split into TestHealthUnauthenticated, TestHealthEndpoint, TestNatsHealthProbe, TestHubTimestamps |
| tests/tts/test_tts_synthesize.py | 1-406 | File exceeds 300 lines (406 lines) | Medium | Extract TestTextNormalization (lines 177-406) to separate file |
| tests/conftest.py | 1-401 | File exceeds 300 lines (401 lines) | Medium | Extract helper functions to tests/helpers/bootstrap_mocks.py |
| tests/cli/test_agent_cli_workflows.py | 32 | `_seed_agent` has 7 parameters | Medium | Use kwargs-only or builder pattern; reduce params to 3-4 |
| tests/conftest.py | 176-245 | `patch_bootstrap_common` and `patch_all` duplicate mock setup | Medium | Extract shared mock setup to a single `_setup_mock_stores` helper |
| tests/test_health_endpoint_status.py | Multiple | Repeated AsyncClient + ASGITransport + create_health_app setup | Low | Create `health_client` fixture returning configured test client |
| tests/tts/test_tts_synthesize.py | 44-66, 71-91, 94-116 | Duplicated tempfile + converter + fake_gen setup | Low | Create `synthesis_test_context` context manager |
| tests/nats/test_nats_bus.py | 320-347, 349-386 | try/finally + async with + wait_for nesting (5+ levels) | Low | Extract test body to helper function or use pytest-asyncio fixtures |
| tests/cli/test_voice_smoke.py | Multiple | Multiple patch + async with nesting (5+ levels) | Low | Use `@pytest.fixture` with `autouse=True` for common patches |
| tests/stt/test_stt_service.py | 192-211, 215-229 | Repeated patch("voicecli.*") context manager stacks | Low | Create `mock_voicecli` fixture or context manager |
| tests/test_circuit_config.py | 24-51, 52-71 | Similar config file setup + assert patterns | Low | Create helper `_assert_circuit_defaults(registry, expected_ids)` |

### Metrics
- Avg function length: 12 lines
- Max function length: 48 lines (tests/nats/test_nats_bus.py `_make_bus` + helper setup)
- God classes: 0 (test classes follow single-concern pattern)
- Files > 300 lines: 7
- Duplication hotspots: 5 (conftest mock setup, health client setup, tts tempfile setup, stt voicecli mocking, circuit config assertions)
- Long parameter lists (>5): 1 (`_seed_agent` with 7 params)
- Deep nesting hotspots: 3 (nats bus tests, voice smoke tests, stt service tests)

### Recommendations

1. **High Priority: Split tests/nats/test_nats_bus.py (894 lines)**
   - Create `tests/nats/test_nats_bus_lifecycle.py` for lifecycle tests
   - Create `tests/nats/test_nats_bus_roundtrip.py` for message transit tests
   - Create `tests/nats/test_nats_bus_publish_only.py` for publish-only mode tests
   - Create `tests/nats/test_nats_bus_version.py` for schema version tests

2. **Medium Priority: Extract shared test fixtures**
   - Create `tests/helpers/async_client.py` with `health_client` fixture
   - Create `tests/helpers/voice_mocks.py` for voicecli mock setup
   - Consolidate conftest.py mock helpers to reduce duplication

3. **Medium Priority: Reduce parameter count in `_seed_agent`**
   - Replace 7 positional params with kwargs pattern:
     ```python
     def _seed_agent(db_path: Path, **fields: Any) -> None:
         defaults = {"name": "testagent", "backend": "claude-cli", ...}
         row = AgentRow(**(defaults | fields))
     ```

4. **Low Priority: Reduce nesting in async tests**
   - Use pytest fixtures with `autouse=True` for common patches
   - Extract nested async test bodies to helper functions
   - Consider pytest-asyncio's `pytest.mark.asyncio` auto-mode

5. **Low Priority: Create assertion helpers**
   - Extract repeated assertion patterns into reusable helpers
   - Example: `_assert_circuit_defaults(registry, names, threshold, timeout)`
