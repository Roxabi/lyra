# Code Smells Analysis: Integration Tests (First Half)

### Summary
The first half of integration tests are well-structured with no functions exceeding 50 lines and no god classes. The primary code smell is significant duplication in `test_command_sessions.py` where three stub functions share nearly identical implementations, and a secondary pattern of duplicated `_FakeTurnStore` classes across multiple test files.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| test_command_sessions.py | 69-123 | DRY violation: Three stub functions (`_stub_vault_add`, `_stub_explain`, `_stub_summarize`) have near-identical 17-line implementations | Medium | Extract to a single parameterized `_stub_session_command` factory function |
| test_session_dm_discord.py | 22-39 | Duplicated `_FakeTurnStore` class - same pattern exists in test_session_clear.py, test_session_reply_to.py, test_session_telegram.py | Low | Extract to tests/helpers/fake_turn_store.py and import across test files |
| test_session_clear.py | 33-58 | Duplicated `_FakeTurnStore` class (same as above) | Low | Same recommendation as above |
| test_command_sessions.py | 126-162 | `make_router_with_session` has 6 parameters (borderline long parameter list) | Low | Consider using a builder pattern or dataclass for configuration |

### Metrics

- **Total files analyzed:** 4
- **Total lines:** 934
- **Avg function length:** ~15 lines
- **Max function length:** 37 lines (`make_router_with_session`)
- **Classes > 300 lines:** 0
- **God classes (≥10 methods):** 0
- **Duplication hotspots:** 2 (stub functions in test_command_sessions.py, _FakeTurnStore across files)
- **Functions > 50 lines:** 0
- **Deep nesting issues (>4 levels):** 0

### Recommendations

1. **High Priority:** Refactor the three stub functions in `test_command_sessions.py` into a single factory:
   ```python
   def make_stub_command(cmd_name: str, usage_msg: str) -> Callable:
       async def _stub(msg, driver, tools, args, timeout):
           if not args:
               return Response(content=usage_msg)
           # ... shared logic
       return _stub
   ```

2. **Medium Priority:** Create `tests/helpers/fake_turn_store.py` to consolidate the duplicated `_FakeTurnStore` class that appears in at least 4 test files.

3. **Low Priority:** Consider extracting test helper factories (`make_message`, `make_mock_driver`, `make_mock_tools`, `_make_dm_message`) to a shared `tests/helpers/` module if they become useful across more test files.
