# Error Handling Analysis: Core Processors, Memory, Auth

### Summary

The core/processors, core/memory, and core/auth areas demonstrate generally solid error handling practices with no bare `except:` clauses and no swallowed exceptions. However, there are 2 instances of generic `Exception` catches (one appropriate as a fallback, one concerning for database consistency), and a notable absence of `finally` blocks for resource cleanup, particularly in the schema migration code which explicitly warns about potential database inconsistency on failure.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| `/home/mickael/projects/lyra/src/lyra/core/memory/memory_schema.py` | 83 | Generic `Exception` catch in schema migration | High | Catch specific SQLite/aiohttp exceptions; wrap remaining as SchemaMigrationError |
| `/home/mickael/projects/lyra/src/lyra/core/memory/memory_schema.py` | 19-87 | Missing `finally` block for cleanup | Medium | Add `finally` to ensure `PRAGMA foreign_keys = ON` is restored even on failure |
| `/home/mickael/projects/lyra/src/lyra/core/memory/memory_schema.py` | 83-87 | DB left in inconsistent state on error | High | Implement transaction rollback or use SAVEPOINT for atomic migration |
| `/home/mickael/projects/lyra/src/lyra/core/processors/vault_add.py` | 97 | Generic `Exception` catch as fallback | Low | Acceptable as safety net after specific `VaultWriteFailed` handling |
| `/home/mickael/projects/lyra/src/lyra/core/processors/_scraping.py` | 40-43 | Silent fallback on DNS resolution failure | Low | Consider logging at debug level for observability |
| `/home/mickael/projects/lyra/src/lyra/core/processors/_scraping.py` | 47-50 | `ValueError` catch is specific | N/A | Good practice - no change needed |
| `/home/mickael/projects/lyra/src/lyra/core/auth/authenticator.py` | 188-195 | Catches `ValueError` with re-raise | N/A | Good practice - enhances error context |

### Metrics

- Try/except blocks: 6 total
- Bare excepts: 0
- Swallowed exceptions: 0
- Generic Exception catches: 2 (1 appropriate fallback, 1 concerning)
- Specific exception catches: 4
- Missing finally blocks: 1 (schema migration)

### Positive Practices Observed

1. **vault_add.py:88-101** - Exception hierarchy handling: catches specific `VaultWriteFailed` first, then generic `Exception` as safety net with logging
2. **authenticator.py:188-195** - ValueError catch with re-raise: preserves error information while adding context
3. **_scraping.py:98-105** - Proper use of domain-specific exception (`ScrapeFailed`) with structured error handling
4. All exception handlers log warnings with `exc_info=True` for debugging

### Recommendations

1. **HIGH PRIORITY**: Refactor `memory_schema.py:apply_schema_compat()` to:
   - Wrap the entire migration in a transaction or SAVEPOINT
   - Add `finally` block to restore `PRAGMA foreign_keys = ON`
   - Catch specific SQLite exceptions (OperationalError, IntegrityError)
   - Define and raise a custom `SchemaMigrationError` for better error propagation

2. **MEDIUM PRIORITY**: Add debug-level logging to `_scraping.py:40-43` when DNS resolution fails, even though the fallback behavior is correct (unresolvable hostname passes through to scraper)

3. **LOW PRIORITY**: Consider defining a custom exception hierarchy for the memory layer to distinguish between:
   - Connection failures
   - Schema migration failures  
   - Query/validation failures
