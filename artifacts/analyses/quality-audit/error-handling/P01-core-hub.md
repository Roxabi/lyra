# Error Handling Analysis: Core Hub

### Summary
The core/hub area demonstrates generally robust error handling with no bare `except:` clauses. Exceptions are consistently logged with context, and cleanup is properly handled via `finally` blocks in critical sections (trace context, temp files, pool IDs). However, several locations use overly broad `Exception` catches that could mask underlying issues, and some error paths lack granular exception context for debugging.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| `/home/mickael/projects/lyra/src/lyra/core/hub/middleware_submit.py` | 94 | Generic `except Exception:` without capturing exception | Medium | Capture as `except Exception as exc` and include `exc` in log message for better debugging |
| `/home/mickael/projects/lyra/src/lyra/core/hub/hub.py` | 209 | Generic `except Exception:` without capturing - pipeline errors are logged but silently swallowed | Medium | Consider capturing and re-raising or adding structured error handling; current pattern may hide root cause in monitoring |
| `/home/mickael/projects/lyra/src/lyra/core/hub/middleware.py` | 64 | Generic `except Exception:` in trace_hook - intentionally swallowed but lacks exception variable | Low | Capture `as exc` for structured logging; fire-and-forget is acceptable but logging should include exc details |
| `/home/mickael/projects/lyra/src/lyra/core/hub/hub_dispatch.py` | 124-130 | Two nested `except Exception as exc:` blocks that log but continue execution - command dispatch errors are handled gracefully but silently | Low | Consider emitting a `CommandFailed` event for observability; current pattern is acceptable for user experience |
| `/home/mickael/projects/lyra/src/lyra/core/hub/hub_circuit_breaker.py` | 55 | Generic `except Exception as exc:` - logs with exception context but dispatch_response failure could be more specific | Low | Already logs with `exc_info=True`; acceptable for fire-and-forget notification |
| `/home/mickael/projects/lyra/src/lyra/core/hub/outbound_errors.py` | 122 | Generic `except Exception as notify_exc:` - notification failure is intentionally swallowed | Low | Already properly logs warning; acceptable design for notification layer |
| `/home/mickael/projects/lyra/src/lyra/core/hub/middleware_stt.py` | 143 | Generic `except Exception as exc:` but properly handles specific STT errors first | Low | Good pattern - catches generic only after checking for STTNoiseError, STTUnavailableError |
| `/home/mickael/projects/lyra/src/lyra/core/hub/middleware_pool.py` | 159 | Generic `except Exception as exc:` - command dispatch failure results in DROP which is handled | Low | Consider more specific exception types if known |

### Metrics
- Try/except blocks: 27
- Bare excepts: 0
- Swallowed exceptions: 0 (all exceptions are logged)
- Generic Exception catches: 9
- Properly used finally blocks: 5 (middleware_guards.py, middleware_pool.py, middleware_stt.py, hub.py, _dispatch.py with BaseException handling)

### Recommendations

1. **High Priority**: Add exception variable capture to `middleware_submit.py:94` and `hub.py:209` - the current pattern loses the exception object which reduces debuggability in production logs.

2. **Medium Priority**: Consider creating custom exception types for pipeline-stage failures (e.g., `PipelineError`, `DispatchError`) to enable more granular error handling instead of catching generic `Exception`.

3. **Low Priority**: Add structured error event emission (`CommandFailed`, `DispatchFailed`) in `hub_dispatch.py` to improve observability without breaking user experience.

4. **Positive Patterns to Preserve**:
   - `_dispatch.py:135` correctly uses `except BaseException` and re-raises `CancelledError`/`KeyboardInterrupt`
   - All `finally` blocks are present for cleanup (TraceContext, temp files, pool IDs)
   - `log.exception()` and `exc_info=True` are consistently used for stack trace preservation
   - STT middleware has proper exception hierarchy handling (STTNoiseError, STTUnavailableError)
