# Error Handling Analysis: Core Agent + CLI

### Summary
The core/agent and core/cli areas demonstrate generally sound error handling practices with no bare `except:` clauses found. However, there are several instances of generic `Exception` catches and swallowed exceptions that could mask underlying issues. The codebase shows good discipline in logging exceptions before swallowing them in most cases, but some patterns lack error context that would aid debugging.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| `/home/mickael/projects/lyra/src/lyra/core/agent/agent_refiner_stages.py` | 43 | Swallowed exception with `pass` (JSON parsing for persona) | Medium | Log the exception at debug level for troubleshooting |
| `/home/mickael/projects/lyra/src/lyra/core/agent/agent_refiner_stages.py` | 48 | Swallowed exception with `pass` (JSON parsing for voice) | Medium | Log the exception at debug level for troubleshooting |
| `/home/mickael/projects/lyra/src/lyra/core/agent/agent.py` | 126 | Generic `Exception` catch silently returns | Medium | Consider catching specific DB exceptions or log at debug level |
| `/home/mickael/projects/lyra/src/lyra/core/agent/agent_commands.py` | 76 | Swallowed `OSError` with `pass` (mtime recording) | Low | Log at debug level; non-critical operation |
| `/home/mickael/projects/lyra/src/lyra/core/cli/cli_protocol.py` | 45 | Broad catch `(asyncio.TimeoutError, Exception)` returns empty string | Medium | Catch specific exceptions; add debug logging |
| `/home/mickael/projects/lyra/src/lyra/core/cli/cli_pool_worker.py` | 74 | Generic `Exception` catch with only debug log | Low | Consider narrowing exception type for callback failures |
| `/home/mickael/projects/lyra/src/lyra/core/cli/cli_pool_worker.py` | 273 | Generic `Exception` catch for `on_reap` callback | Low | Callback failures are logged with `exc_info=True` - acceptable |
| `/home/mickael/projects/lyra/src/lyra/core/cli/cli_pool_session.py` | 50 | Swallowed `RuntimeError` (no running loop) | Low | Intentional fallback for test contexts - acceptable |
| `/home/mickael/projects/lyra/src/lyra/core/cli/cli_streaming.py` | 165 | Generic `Exception` catch for pool reset callback | Low | Logged with `exc_info=True` - acceptable defensive code |
| `/home/mickael/projects/lyra/src/lyra/core/cli/cli_non_streaming.py` | 205 | Top-level `Exception` catch returns error result | Low | Appropriate for protocol layer; could narrow exception types |
| `/home/mickael/projects/lyra/src/lyra/core/cli/cli_pool.py` | 186 | Generic `Exception` catch kills pool | Medium | Consider catching specific I/O and process exceptions |

### Metrics
- Try/except blocks: 31 (11 in agent, 20 in cli)
- Bare excepts: 0
- Swallowed exceptions (pass/return only): 6
- Generic Exception catches: 11
- finally blocks: 1 (in cli_protocol.py:87-88 for fd cleanup)
- CancelledError handled properly: 2 (cli_pool_lifecycle.py:84, cli_pool_worker.py:279)

### Recommendations

1. **High Priority - Add debug logging to swallowed exceptions in `agent_refiner_stages.py`**
   - Lines 43-44 and 48-49 catch `Exception` and silently `pass`
   - These JSON parsing failures could indicate malformed data that would be useful to debug
   ```python
   except Exception as exc:
       log.debug("Failed to parse persona_json for %r: %s", ctx.agent_name, exc)
   ```

2. **Medium Priority - Narrow generic Exception catches in protocol layer**
   - `cli_protocol.py:45` catches `(asyncio.TimeoutError, Exception)` - consider catching specific I/O exceptions
   - `cli_non_streaming.py:205` catches all exceptions - narrow to expected I/O and protocol errors

3. **Medium Priority - Add debug logging to `agent.py:126`**
   - The DB unavailable catch silently returns; adding debug logging would help diagnose DB connection issues

4. **Low Priority - Document intentional exception suppression**
   - Several catches have `# noqa: BLE001` comments but could benefit from inline comments explaining the rationale
   - Example: `cli_pool_worker.py:144-145` - the `pass` for `asyncio.TimeoutError` is the happy path

5. **Good Practices Observed**
   - No bare `except:` clauses anywhere
   - `asyncio.CancelledError` is properly handled in async contexts
   - Most catches log the exception with `exc_info=True` before suppressing
   - The one `finally` block in `cli_protocol.py` properly closes the file descriptor
   - Plugin loading failures don't crash the agent (resilient design)
