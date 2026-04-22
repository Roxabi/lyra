### Summary

The processors, memory, and auth modules are generally well-maintained with no TODO/FIXME comments. However, several tech debt items were identified: one deprecated API pattern in command_router.py, hardcoded magic numbers for timeouts/limits across 6 locations, duplicate datetime parsing logic in memory_freshness.py, a broad exception handler in memory_schema.py, and multiple noqa suppressions for complexity (C901, PLR0913) indicating opportunities for refactoring.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| `commands/command_router.py` | 149 | Deprecated API: `register_session_command()` should use `@register` decorator from processor_registry | Medium | Add migration guide or deprecation warning to callers |
| `_scraping.py` | 26 | Magic number: `_SAFE_SCRAPE_MAX_CHARS = 32_000` | Low | Extract to config constant |
| `_scraping.py` | 127 | Magic number: `timeout=30.0` hardcoded | Low | Use shared constant or config |
| `vault_add.py` | 89 | Magic number: `timeout=30.0` hardcoded | Low | Use shared constant or config |
| `search.py` | 41 | Magic number: `timeout=25.0` hardcoded | Low | Use shared constant or config |
| `memory.py` | 59, 145, 164 | Magic numbers: `limit=1`, `limit=8`, `limit=10` | Low | Extract to named constants |
| `memory.py` | 70 | Magic number: `token_budget: int = 1000` | Low | Extract to config constant |
| `memory_freshness.py` | 30, 41 | Duplicate code: identical datetime parsing `datetime.fromisoformat(updated_str.replace("Z", "+00:00"))` | Medium | Extract to helper function |
| `memory_schema.py` | 83 | Broad exception: `except Exception:` catches all exceptions | Medium | Catch specific SQLite exceptions or log with structured error |
| `memory_schema.py` | 14 | Missing type annotation: `db` param has `# noqa: ANN001` | Low | Add proper type hint for aiosqlite Connection |
| `stream_processor.py` | 87 | Complexity: `process()` method has `# noqa: C901` (cyclomatic complexity) | Medium | Consider extracting event handlers to separate methods |
| `authenticator.py` | 40, 244 | Parameter count: `__init__` and `from_bot_config` have `# noqa: PLR0913` (too many params) | Low | Consider options dataclass pattern |
| `vault_add.py` | 97 | Broad exception: `except Exception as exc:` catches unexpected errors | Low | Log and re-raise or handle specific cases |
| `authenticator.py` | 25-30 | Manual trust ordering: `_TRUST_ORDER` dict instead of enum-native ordering | Low | Could use `IntEnum` or `__lt__` on TrustLevel |

### Metrics

- TODOs: 0
- FIXMEs: 0
- Dead code lines: 0
- Deprecated patterns: 1
- Magic numbers/strings: 9
- Duplicate code blocks: 1
- Broad exception handlers: 2
- Complexity suppressions (noqa): 4

### Recommendations

1. **High priority**: Document migration path from `register_session_command()` to `@register` decorator in processor_registry. Add runtime deprecation warning.

2. **Medium priority**: Extract duplicate datetime parsing in `memory_freshness.py` to a shared helper function `_parse_iso_datetime(updated_str: str) -> datetime | None`.

3. **Medium priority**: Refactor `StreamProcessor.process()` to reduce cyclomatic complexity. Consider extracting the three event-type branches (TextLlmEvent, ToolUseLlmEvent, ResultLlmEvent) into dedicated handler methods.

4. **Low priority**: Create a constants module or config section for timeout values (25.0, 30.0) and limits (1, 8, 10, 1000) to enable centralized tuning.

5. **Low priority**: Replace broad `except Exception` in `memory_schema.py` with `except sqlite3.Error` or similar specific exceptions, with proper error context in the log.

6. **Low priority**: Add type annotation for aiosqlite Connection parameter in `apply_schema_compat()` - use `from typing import Any` and document the expected duck-typed interface.
