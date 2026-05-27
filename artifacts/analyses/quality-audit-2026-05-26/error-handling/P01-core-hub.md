## Error Handling Audit — P01: `src/lyra/core/hub/**/*.py`

### Summary

- **10 broad `except Exception` / `except BaseException` blocks** (43 % of all catches); none use empty `pass`, but 3 sit on paths that can silently degrade UX or mask infrastructure failures.
- **Zero `str(exc)` leaks into user-visible text** — partition consistently routes errors through generic template keys (`_SEND_ERROR_MSG`, `GENERIC_ERROR_REPLY`, `stt_*` fallbacks). Good.
- **1 structural crash risk**: `pool.submit()` in `_dispatch_pipeline_result` is unguarded; a synchronous raise terminates the `Hub.run()` consumer loop, while `pipeline.process()` errors are swallowed.

### Findings

| File | Line | Severity | Description | Recommendation |
|---|---|---|---|---|
| `hub.py` | 206-210 | Medium | `except Exception` swallows all pipeline stage errors; message is silently dropped with no user feedback and no `MessageDropped` event | Emit `MessageDropped` event + dispatch generic error reply via adapter |
| `hub.py` / `hub_dispatch.py` | 212 / 136 | High | `_dispatch_pipeline_result` does not protect `pool.submit()`; a synchronous exception propagates and crashes the `Hub.run()` loop | Wrap `pool.submit()` in try/except, log, and continue the loop |
| `middleware_submit.py` | 90-97 | Medium | `except Exception` swallows `resolve_context` failures (TurnStore / MessageIndex); masks outages as `ResumeStatus.SKIPPED` | Narrow to store-specific exceptions (`StoreError`, `ConnectionError`) or emit health metric |
| `middleware_pool.py` | 189-198 | Low-Medium | `except Exception` on command dispatch is broad; returns generic reply (good) but may swallow unintended bugs | Introduce domain `CommandError` to avoid catching programming errors |
| `hub_dispatch.py` | 121, 128 | Low | `except Exception` swallows audio/response dispatch errors in pipeline-result path; no user notification for partial failures | Add `try_notify_user` fallback for partial dispatch failures |
| `hub_shutdown.py` | 101-112 | Low | Sequential `close()` without per-store guards; if `_memory.close()` raises, `_turn_store` and `_message_index` are never closed | Wrap each `close()` in an individual try/except block |
| `hub_circuit_breaker.py` | 53-56 | Low | `except Exception` swallows fast-fail dispatch failure (already degraded path) | Acceptable as-is; consider bounded retry for critical platforms |

### Metrics

| Metric | Count | % |
|---|---|---|
| Total `except` clauses | 23 | 100 % |
| Broad catches (`except Exception` / `except BaseException`) | 10 | 43 % |
| Specific catches (`ValueError`, `TimeoutError`, `QueueFull`) | 13 | 57 % |
| Bare `except:` | 0 | 0 % |
| Swallowed with empty `pass` | 0 | 0 % |
| `str(exc)` leaks into user-visible messages | 0 | 0 % |
| Missing `raise ... from e` (wrap-and-re-raise gaps) | 0 | 0 % |
| Retry loops with exponential backoff + circuit breaker | 1 | — |

### Recommendations (prioritized, max 5)

1. **Protect `pool.submit()` in `_dispatch_pipeline_result`** — add try/except around the call to prevent a single bad pool from crashing the entire hub consumer loop.
2. **Surface swallowed pipeline errors to users** — when `pipeline.process()` raises inside `Hub.run()`, dispatch a generic error response (e.g., `Response(content=GENERIC_ERROR_REPLY)`) instead of silently dropping the message.
3. **Narrow `resolve_context` exception handling** — replace `except Exception` in `SubmitToPoolMiddleware` with catches for known store/transport exceptions; an unqualified catch makes TurnStore or MessageIndex outages indistinguishable from normal skipped resumes.
4. **Guard individual store shutdowns** — wrap `_memory.close()`, `_turn_store.close()`, and `_message_index.close()` each in their own try/except so a failure in one does not leak resources in the others.
5. **Introduce a domain exception for command dispatch** — replace `except Exception` in `CommandMiddleware._dispatch_command` with `except CommandError` (or similar) so unintended bugs in command handlers are not masked by the generic-error fallback.
