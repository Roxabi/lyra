# Async Patterns Analysis: Core Processors, Memory, Auth

### Summary
The `core/processors`, `core/memory`, and `core/auth` modules contain 20 async functions with generally well-structured async patterns. However, one significant blocking call exists in the scraping processor that can stall the event loop during DNS lookups. The memory layer lacks transaction management for multi-step upserts, and schema migration error handling leaves the database in a potentially inconsistent state.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| src/lyra/core/processors/_scraping.py | 39 | Blocking `socket.getaddrinfo()` call inside async execution path - DNS lookup can block event loop for seconds | High | Use `asyncio.to_thread()` to offload DNS lookup, or use `asyncio.get_running_loop().getaddrinfo()` if available, or pre-resolve in a thread pool |
| src/lyra/core/memory/memory_schema.py | 29-82 | Schema migration performs multiple DDL operations without transaction wrapping; `PRAGMA foreign_keys = OFF` at line 29 leaves DB in unsafe state if migration fails | Medium | Wrap migration in savepoint/transaction; restore `PRAGMA foreign_keys = ON` in finally block; re-raise or implement rollback |
| src/lyra/core/memory/memory_schema.py | 83-87 | Bare `except Exception` catches all and only logs warning - database left in potentially corrupted state (tables dropped, FK disabled) | Medium | Implement recovery logic or re-raise after logging; use specific exception types |
| src/lyra/core/memory/memory_upserts.py | 113-180 | `upsert_concept` performs SELECT then UPDATE/INSERT without transaction - concurrent requests could cause race conditions or partial updates | Medium | Use `BEGIN IMMEDIATE` transaction or SQLite `INSERT OR REPLACE` pattern; wrap in `async with db.execute("BEGIN")` / commit block |
| src/lyra/core/memory/memory_upserts.py | 182-238 | `upsert_preference` has same SELECT-then-modify race condition as `upsert_concept` | Medium | Same as above - use transaction or atomic upsert pattern |
| src/lyra/core/memory/memory_upserts.py | 77-109 | `upsert_contact` has SELECT-then-UPDATE/INSERT race condition | Medium | Same as above - use transaction or atomic upsert pattern |
| src/lyra/core/processors/vault_add.py | 97-101 | Broad `except Exception` catches unexpected errors silently - could mask programming errors | Low | Narrow exception types; at minimum log stack trace with `exc_info=True` (already done) - consider adding metrics/monitoring hook |
| src/lyra/core/processors/_scraping.py | 83-86 | SSRF DNS check has TOCTOU window - DNS resolution at scrape time may differ from validation time | Low | Document that scraper validates again; consider caching DNS result briefly or passing validated IP to scraper |

### Metrics
- Async functions: 20 (processors: 7, memory: 13, auth: 0)
- Blocking calls in async: 1 (`socket.getaddrinfo` in `_is_private_ip`)
- Potential race conditions: 1 (TOCTOU in DNS check, mitigated by scraper re-resolution)
- Missing transaction management: 3 (upsert_concept, upsert_preference, upsert_contact)
- Broad exception handlers: 2 (memory_schema, vault_add)

### Recommendations

1. **High Priority - Fix Blocking DNS Call** (`_scraping.py`):
   ```python
   # Option A: Use asyncio thread pool
   results = await asyncio.to_thread(socket.getaddrinfo, hostname, None)

   # Option B: Use loop.getaddrinfo (if available)
   loop = asyncio.get_running_loop()
   results = await loop.getaddrinfo(hostname, None)
   ```

2. **Medium Priority - Add Transaction Management** (`memory_upserts.py`):
   - Wrap SELECT+UPDATE/INSERT sequences in explicit transactions
   - Use `BEGIN IMMEDIATE` to acquire write lock before SELECT
   - Or restructure as single atomic `INSERT OR REPLACE` statements

3. **Medium Priority - Fix Schema Migration Safety** (`memory_schema.py`):
   - Add `finally` block to restore `PRAGMA foreign_keys = ON`
   - Consider using `SAVEPOINT` for rollback capability
   - Re-raise exception or implement recovery logic

4. **Low Priority - Narrow Exception Handling** (`vault_add.py`):
   - Keep specific `VaultWriteFailed` handling
   - Consider separating expected vs unexpected exceptions with different log levels
