# Error Handling Analysis: Core Commands, Stores, Pool, Messaging

### Summary
The core commands, stores, pool, and messaging modules demonstrate a **defensive error handling philosophy** with consistent logging patterns, but exhibit several areas for improvement. The codebase contains **no bare `except:` clauses** (excellent), but has **17 generic `Exception` catches** across the analyzed files. Several instances of **silently swallowed exceptions** exist, primarily in the pool layer where observability callbacks intentionally fail gracefully. Error propagation is generally well-structured with specific exception types raised, but some modules lack proper error context in logging.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| builtin_commands.py | 222-223 | Swallowed exception (`except OSError: pass`) - file deletion failure silently ignored | Medium | Log the OSError at debug level for troubleshooting |
| pool_observer.py | 84-90 | Generic `Exception` catch with `exc_info=True` - acceptable but catches too broadly | Low | Consider catching specific store exceptions |
| pool_observer.py | 116-122 | Generic `Exception` catch in turn logging - silent failure mode | Low | Document intentional graceful degradation |
| pool_observer.py | 131-136 | Generic `Exception` catch in session update | Low | Add structured error type for session failures |
| pool_observer.py | 153-158 | Generic `Exception` catch in turn logger | Low | Consider typed exception hierarchy |
| pool_observer.py | 187-192 | Generic `Exception` catch in message index upsert | Low | Add retry logic or circuit breaker |
| pool_processor.py | 52-58 | Generic `Exception` catch for session resume - good logging but broad | Low | Acceptable for resilience, document intent |
| pool_processor.py | 150-155 | Broad catch `(asyncio.QueueFull, Exception)` - message loss scenario | Medium | Split into specific handlers; log QueueFull separately |
| pool_processor_exec.py | 78-88 | Generic `Exception` catch - proper handling with error reply | Low | Good pattern, consider typed errors |
| pool_processor_exec.py | 145-148 | Generic `Exception` for processor pre() - good error surface | Low | Acceptable defensive pattern |
| pool_processor_exec.py | 170-171 | Generic `Exception` for processor post() - logged but silent | Low | Document intentional graceful failure |
| pool_processor_exec.py | 270-271 | Generic `Exception` in `_safe_dispatch` - logged with context | Low | Good pattern |
| pool.py | 228-233 | Generic `Exception` for resume callback - logged with exc_info | Low | Acceptable for resilience |
| pool.py | 251-252 | Generic `Exception` for start_session - logged | Low | Consider typed exception |
| pool_processor_streaming.py | 116-117 | Generic `Exception` for processor post() streaming | Low | Consistent with non-streaming pattern |
| pairing.py | 208-213 | Generic `Exception` for AuthStore upsert - logged with user-facing error | Medium | Return typed error instead of generic string |
| command_loader.py | 114-116 | Generic `Exception` with `noqa: BLE001` for malformed TOML | Low | Acceptable for discovery/resilience |
| command_router.py | 116-117 | Generic `Exception` for processor registry import | Low | Acceptable for optional feature |
| messages.py | 45-47 | Generic `Exception` for TOML load failure - silent fallback | Low | Intentional degradation, acceptable |
| messages.py | 54-58 | Generic `Exception` for template resolution - returns fallback | Low | "Never raises" contract, acceptable |
| json_agent_store.py | 228-235 | OSError catch and re-raise with context - **good pattern** | N/A | Model for other modules |
| agent_store_migrations.py | 29-31 | Specific `OperationalError` catch with conditional re-raise | N/A | **Excellent pattern** |

### Metrics
- Try/except blocks: 40 total (13 commands + 5 stores + 19 pool + 3 messaging)
- Bare excepts: 0
- Generic Exception catches: 17
- Swallowed exceptions (pass in except): 1 (builtin_commands.py:222)
- Finally blocks: 3 (all in pool layer, appropriate placement)
- Specific exception catches: 12 (FileNotFoundError, OSError, KeyError, TypeError, ValueError, asyncio.TimeoutError, asyncio.CancelledError, asyncio.QueueFull, asyncio.QueueEmpty, json.JSONDecodeError, aiosqlite.OperationalError, BaseException)

### Recommendations

1. **High Priority - Fix swallowed exception in builtin_commands.py**
   - Line 222-223: Replace `except OSError: pass` with `except OSError as exc: log.debug("Failed to delete runtime file: %s", exc)`
   - Silent failures in admin operations should be traceable.

2. **Medium Priority - Improve error typing in pool layer**
   - Create a `PoolError` hierarchy with `SessionError`, `TurnLogError`, `DispatchError` subtypes
   - This enables callers to distinguish between transient and fatal failures

3. **Medium Priority - Add error context to pairing.py**
   - Line 208-213: Return a typed error response with structured data rather than generic string
   - Enables API clients to programmatically handle the failure

4. **Low Priority - Document graceful degradation patterns**
   - PoolObserver methods intentionally swallow errors to avoid disrupting user experience
   - Add docstring notes explaining the intentional silent failure mode

5. **Low Priority - Split broad exception catches**
   - pool_processor.py line 150: Split `(asyncio.QueueFull, Exception)` into separate handlers
   - QueueFull is expected behavior; other exceptions indicate bugs

6. **Maintenance - Add # noqa comments with justification**
   - command_loader.py already has `# noqa: BLE001` with comment - extend this pattern
   - Ensures linters don't flag intentional broad catches

### Positive Patterns Observed

- **No bare `except:` clauses** - excellent discipline
- **Consistent logging with `exc_info=True`** in catch blocks
- **Proper `asyncio.CancelledError` handling** - always re-raised after cleanup
- **`BaseException` used correctly** in pairing.py:194 for transaction rollback
- **Finally blocks for cleanup** in pool_processor.py, pool_processor_exec.py, pool_processor_streaming.py
- **Specific exception types** raised for validation errors (ValueError, TypeError, RuntimeError)
- **Good error context in logging** - pool_id, session_id, agent_name consistently included
