# Async Patterns Analysis: P4 Partition (Processors, Memory, Auth)

### Summary
The `core/processors`, `core/memory`, and `core/auth` modules demonstrate mature async patterns with proper use of `await`, correct offloading of blocking DNS calls via `asyncio.to_thread()`, and a well-designed reuse guard in `StreamProcessor`. However, the memory layer lacks transaction management for multi-step upserts, and the schema migration has incomplete error recovery that could leave the database in an inconsistent state.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| src/lyra/core/memory/memory_schema.py | 29-82 | Schema migration performs multiple DDL operations with `PRAGMA foreign_keys = OFF` but lacks a `finally` block to restore it on failure | Medium | Wrap migration in try/finally to ensure `PRAGMA foreign_keys = ON` is always restored |
| src/lyra/core/memory/memory_schema.py | 83-87 | Bare `except Exception` catches all errors and only logs warning - database left in potentially corrupted state (tables dropped, FK disabled) | Medium | Implement recovery logic or re-raise after logging; use specific exception types |
| src/lyra/core/memory/memory_upserts.py | 113-180 | `upsert_concept` performs SELECT then UPDATE/INSERT without transaction - concurrent requests could cause race conditions or partial updates | Medium | Use `BEGIN IMMEDIATE` transaction or SQLite `INSERT OR REPLACE` pattern |
| src/lyra/core/memory/memory_upserts.py | 182-238 | `upsert_preference` has same SELECT-then-modify race condition as `upsert_concept` | Medium | Same as above - use transaction or atomic upsert pattern |
| src/lyra/core/memory/memory_upserts.py | 77-109 | `upsert_contact` has SELECT-then-UPDATE/INSERT race condition | Medium | Use transaction or atomic upsert pattern |
| src/lyra/core/memory/memory_upserts.py | 47-53 | `save_identity_anchor` performs separate SELECT then INSERT/UPDATE without transaction | Low | Wrap in transaction or use `INSERT OR REPLACE` |
| src/lyra/core/memory/memory.py | 49-54 | `MemoryManager` has `connect()`/`close()` methods but does not implement `__aenter__`/`__aexit__` - callers may forget to close | Low | Implement async context manager protocol for proper resource management |
| src/lyra/core/processors/processor_registry.py | 129-131 | `clear()` method documented as "not thread-safe" for test use but no synchronization - acceptable for test-only but worth documenting | Info | Already documented; consider adding `@pytest.fixture` wrapper in tests |
| src/lyra/core/processors/vault_add.py | 97-101 | Broad `except Exception` catches unexpected errors - already logs with `exc_info=True` which is good practice | Info | Consider separating expected vs unexpected exceptions with different log levels |
| src/lyra/core/processors/_scraping.py | 39 | GOOD PATTERN: `asyncio.to_thread(socket.getaddrinfo, ...)` correctly offloads blocking DNS lookup | None | N/A - exemplary pattern |
| src/lyra/core/processors/stream_processor.py | 191-198 | GOOD PATTERN: `_consumed` reuse guard prevents accidental reuse of stateful processor across turns | None | N/A - exemplary pattern |

### Metrics
- Files analyzed: 16
- Race conditions: 4 (all in memory_upserts.py SELECT-then-modify patterns)
- Blocking calls: 0 (DNS lookup correctly offloaded via asyncio.to_thread)
- Resource leaks: 1 (MemoryManager lacks context manager protocol)
- Missing awaits: 0

### Recommendations

1. **Medium Priority - Add Transaction Management** (`memory_upserts.py`):
   - Wrap SELECT+UPDATE/INSERT sequences in explicit transactions
   - Use `BEGIN IMMEDIATE` to acquire write lock before SELECT
   - Or restructure as single atomic `INSERT OR REPLACE` statements
   ```python
   async with db.execute("BEGIN IMMEDIATE"):
       # SELECT then UPDATE/INSERT
       await db.commit()
   ```

2. **Medium Priority - Fix Schema Migration Safety** (`memory_schema.py`):
   - Add `finally` block to restore `PRAGMA foreign_keys = ON`
   - Consider using `SAVEPOINT` for rollback capability within migration
   ```python
   try:
       await db.execute("PRAGMA foreign_keys = OFF")
       # ... migration steps ...
   finally:
       await db.execute("PRAGMA foreign_keys = ON")
   ```

3. **Low Priority - Implement Context Manager** (`memory.py`):
   - Add `__aenter__`/`__aexit__` to `MemoryManager` for proper resource cleanup
   - Enables `async with MemoryManager(path) as mm:` pattern
