# Async Patterns Analysis: Core Commands, Stores, Pool, Messaging

### Summary
The analyzed modules demonstrate mature async patterns with proper use of `aiosqlite` for async database operations, correct handling of `asyncio` primitives, and appropriate transaction management. Two notable issues were identified: blocking I/O in `json_agent_store.py` async methods (intentional for testing) and a redundant `asyncio.create_task` wrapper in streaming post-processing. Overall async hygiene is good.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| `/home/mickael/projects/lyra/src/lyra/core/stores/json_agent_store.py` | 71 | Blocking I/O: `self._path.read_text()` in async `connect()` method | Medium | For production code, use `aiofiles` or `asyncio.to_thread()`. Acceptable for test stub as documented. |
| `/home/mickael/projects/lyra/src/lyra/core/stores/json_agent_store.py` | 227 | Blocking I/O: `self._path.write_text()` in `_persist()` called from async write methods | Medium | Same as above. Intentional for test store - file I/O is typically small JSON payloads. |
| `/home/mickael/projects/lyra/src/lyra/core/pool/pool_processor_streaming.py` | 115 | Redundant pattern: `await asyncio.create_task(processor.post(...))` | Low | Simplify to `await processor.post(...)`. The task wrapper adds no value when immediately awaited. |
| `/home/mickael/projects/lyra/src/lyra/core/stores/pairing.py` | 194 | Broad exception catch `except BaseException` in transaction cleanup | Info | Actually correct - ensures ROLLBACK on CancelledError. Good defensive pattern. |
| `/home/mickael/projects/lyra/src/lyra/core/commands/command_loader.py` | 114 | Broad exception `except Exception` with noqa comment | Info | Intentional resilience for plugin loading. Appropriate use. |
| `/home/mickael/projects/lyra/src/lyra/core/commands/command_router.py` | 292 | Catches `TimeoutError` instead of `asyncio.TimeoutError` | Low | Python 3.11+ alias, but explicit `asyncio.TimeoutError` is clearer for older compatibility. |

### Metrics
- **Async functions analyzed**: 52
- **Blocking calls in async**: 2 (both in test-only JsonAgentStore)
- **Potential race conditions**: 0 (proper locking with `BEGIN IMMEDIATE` and `asyncio.Lock`)
- **Missing `await` keywords**: 0
- **Resource leaks detected**: 0 (all stores implement `close()`, proper `async with` usage)
- **Redundant async patterns**: 1

### Positive Patterns Observed
1. **Transaction safety**: `pairing.py` uses `BEGIN IMMEDIATE` to prevent TOCTOU races in pairing code validation
2. **Proper cancellation handling**: `pool_processor.py` uses `asyncio.shield()` for safe dispatch during cancellation
3. **Timeout enforcement**: Consistent use of `asyncio.wait_for()` with configurable timeouts
4. **Resource cleanup**: All stores inherit from `SqliteStore` with proper `connect()`/`close()` lifecycle
5. **Async generator cleanup**: `pool_processor_streaming.py` properly calls `aclose()` in finally block

### Recommendations

1. **Priority 1 (Low)**: Simplify redundant `asyncio.create_task` in `pool_processor_streaming.py:115`:
   ```python
   # Current
   await asyncio.create_task(processor.post(original_msg, streamed))
   # Recommended
   await processor.post(original_msg, streamed)
   ```

2. **Priority 2 (Documentation)**: Add docstring note to `JsonAgentStore` clarifying that blocking I/O is intentional for test environments and should not be used as a pattern for production code.

3. **Priority 3 (Style)**: Use explicit `asyncio.TimeoutError` in `command_router.py:292` for clarity, though functionally equivalent in Python 3.11+.

4. **No action needed**: The `except BaseException` pattern in `pairing.py` is correct and necessary for proper transaction rollback on task cancellation.
