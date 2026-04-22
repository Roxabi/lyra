# Async Patterns Analysis: Core Agent + CLI

### Summary

The core/agent and core/cli modules contain 26 async functions across 10 files. The codebase demonstrates mature async patterns overall with proper use of `asyncio.Lock`, `async with` context managers, and timeout handling. However, several issues were identified: a fire-and-forget task pattern that could lose exceptions, one instance of `asyncio.run()` in a potentially unsafe context, and broad exception handling that could mask errors.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| /home/mickael/projects/lyra/src/lyra/core/cli/cli_pool_session.py | 47-51 | Fire-and-forget task - `create_task()` without storing reference or awaiting | Medium | Store task reference and add error callback, or use `asyncio.gather()` with proper cleanup |
| /home/mickael/projects/lyra/src/lyra/core/agent/agent_refiner.py | 279 | `asyncio.run()` called from sync method `apply_patch()` - fails if called from async context | Medium | Document this is CLI-only, or provide async variant `apply_patch_async()` |
| /home/mickael/projects/lyra/src/lyra/core/cli/cli_pool_worker.py | 74 | Broad `except Exception:` silently swallows session update callback errors | Low | Log at debug level or add structured error reporting |
| /home/mickael/projects/lyra/src/lyra/core/cli/cli_pool_worker.py | 133 | Broad exception catch during spawn - returns None hiding spawn failure details | Low | Include exception details in log message for debugging |
| /home/mickael/projects/lyra/src/lyra/core/cli/cli_streaming.py | 165 | Broad `except Exception:` in `_cleanup()` hides pool reset failures | Low | Already logs warning - consider adding structured metrics |
| /home/mickael/projects/lyra/src/lyra/core/cli/cli_protocol.py | 45 | `except (asyncio.TimeoutError, Exception)` - redundant catch-all | Low | Simplify to just `except Exception` or remove TimeoutError from tuple |
| /home/mickael/projects/lyra/src/lyra/core/cli/cli_non_streaming.py | 205 | Exception caught but only type name logged, not full traceback | Low | Use `log.exception()` instead of `log.exception()` with manual type extraction |
| /home/mickael/projects/lyra/src/lyra/core/agent/agent.py | 126 | Bare `except Exception:` silently ignores DB reload failures | Low | Add debug logging for transparency |
| /home/mickael/projects/lyra/src/lyra/core/agent/agent_seeder.py | 52 | Blocking `open()` call in async function `seed_from_toml()` | Low | For CLI seeder this is acceptable; document if perf-critical path |
| /home/mickael/projects/lyra/src/lyra/core/agent/agent_refiner.py | 111 | Blocking `input()` call in sync `TerminalIO.prompt()` used in interactive session | Info | Acceptable for CLI tool - not an async context issue |

### Metrics

- **Async functions**: 26 (7 in agent/, 19 in cli/)
- **Blocking calls in async**: 1 (file read in agent_seeder.py)
- **Fire-and-forget tasks**: 1 (cli_pool_session.py)
- **Broad exception handlers**: 7
- **Potential race conditions**: 0 (locking appears sound)
- **Resource leaks**: 0 (temp files cleaned in `_kill()`, processes tracked in `_entries`)

### Recommendations

1. **High Priority - Fix fire-and-forget task (cli_pool_session.py)**
   - Line 47 creates a background task without tracking it
   - If `set_cli_session()` raises an exception, it will be silently lost
   - Solution: Store task reference in a set, add `done_callback` for error logging, clean up completed tasks

2. **Medium Priority - Document `asyncio.run()` usage (agent_refiner.py)**
   - Line 279 uses `asyncio.run()` which creates a new event loop
   - This will raise RuntimeError if called from an already-running async context
   - Add docstring warning: "CLI use only - do not call from async code"
   - Consider providing `async def apply_patch_async()` for callers in async context

3. **Low Priority - Improve exception logging**
   - Several locations use `except Exception:` with just `pass` or minimal logging
   - Add debug-level logs to aid troubleshooting
   - Consider structured logging with exception details

4. **Low Priority - Reduce exception scope**
   - Replace broad `except Exception:` with specific exception types where the handling logic is known
   - For truly resilient paths (plugin loading, hot-reload), keep broad catch but add structured metrics

### Positive Patterns Observed

- Proper use of `asyncio.Lock` with `async with` context managers
- Consistent timeout handling with `asyncio.wait_for()`
- Clean process lifecycle management with reaper task cancellation
- Good separation of streaming/non-streaming protocol layers
- Well-documented locking model in CliPool (pool.lock vs entry._lock)
