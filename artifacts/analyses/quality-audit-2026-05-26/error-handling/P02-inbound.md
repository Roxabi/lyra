# Error Handling Quality Audit — P02 Inbound (2026-05-26)

Scope: `src/lyra/inbound/**/*.py` (9 files)

---

### Summary

- **3 broad `RuntimeError` catch sites in `session_builder.py`** swallow store I/O failures silently (session reads + writes). `RuntimeError` is used by the SQLite store layer for lifecycle errors (e.g. "not connected"), but catching it broadly also masks unrelated programming bugs (coroutine misuse, event-loop issues, etc.).
- **1 `ValueError` fallback in `_platform_enum`** silently defaults unknown platform strings to `Platform.TELEGRAM`, masking adapter/platform misconfiguration.
- **No `str(exc)` leaks, no retry logic, no bare `except:` / `except Exception:` without re-raise** found inside the inbound partition. `pipeline.py` and `dispatcher.py` contain no explicit try/except boundaries.

---

### Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `session_builder.py` | 95 | Medium | `except (sqlite3.Error, RuntimeError)` swallows broad `RuntimeError` during `TurnStore.get_last_session`; `_prior_session_id` stays `None` and the turn starts fresh without context. | Split catches: handle `sqlite3.Error` as graceful degradation; let `RuntimeError` propagate (or narrow the store layer to a custom exception hierarchy). |
| `session_builder.py` | 170 | Medium | `except (sqlite3.Error, RuntimeError)` swallows broad `RuntimeError` during `ThreadStore.get_session`; thread session is lost and the turn starts fresh. | Same as above. If `RuntimeError` is intentionally caught for "not connected" state, the store layer should raise a store-specific exception instead of the stdlib base. |
| `session_builder.py` | 212 | High | `except (sqlite3.Error, RuntimeError)` inside `_thread_update_fn` closure swallows `ThreadStore.update_session` failures; the new session_id is never persisted to the store or cache. This is a silent data-loss path. | Narrow the catch to store-specific exceptions. Consider propagating the failure to the turn caller so the hub knows persistence failed. |
| `session_builder.py` | 234 | Low | `except ValueError` in `_platform_enum` silently defaults unknown platform strings to `Platform.TELEGRAM`; misconfiguration is only a log warning. | Make the function raise on unknown platform, or at minimum emit a metric/alert on the fallback path so misconfiguration is surfaced operationally. |

---

### Metrics

| Metric | Count |
|--------|-------|
| Total Python files | 9 |
| Files with try/except | 1 (11%) |
| Exception catch sites | 4 |
| Swallowed exceptions (log + continue) | 4 |
| Broad `RuntimeError` catches without re-raise | 3 |
| `str(exc)` leaks into user-visible text | 0 |
| Missing `raise ... from e` | 0 (nothing re-raised in partition) |
| Retry/backoff/circuit-breaker issues | 0 (no retry logic in partition) |

---

### Recommendations (prioritized)

1. **Replace broad `RuntimeError` catches with store-specific exceptions** (High). The `sqlite_base.py` / `turn_store.py` store layer raises `RuntimeError("call connect() first")` and `RuntimeError("TurnStore not connected")`. Introduce a narrow `StoreNotConnectedError` (or similar) inheriting from a shared store exception base. This lets `session_builder.py` catch only legitimate store-unavailable errors without masking programming bugs.

2. **Surface `_thread_update_fn` persistence failures** (High). The closure returned in `_build_thread_path` (line 212) currently logs and silently drops `ThreadStore.update_session` errors. Because this closure executes inside the turn handler long after `SessionBuilder.build` returns, a silent failure means the thread cache and store diverge. Consider returning a persistence result (success/failure) or raising a non-retryable exception so the turn pipeline can react.

3. **Fail hard on unknown platform in `_platform_enum`** (Low). Defaulting to `Platform.TELEGRAM` on `ValueError` is a defensive convenience that can mask adapter wiring bugs. Change `_platform_enum` to raise `ValueError` (or a custom `PlatformUnknownError`) and let the caller decide whether to drop the message or crash. This aligns with `Router.decide`'s design: unknown `PlatformMeta` subclass → `DROP`.

4. **Document the swallowed-read degradation contract** (Low). The docstrings in `SessionBuilder.build` and `_build_thread_path` describe graceful fallback when the store returns an unresolved `ThreadSession`, but they do not mention the exception-swallowing path. Document that `sqlite3.Error` (and ideally the future narrow exception) triggers the same "fresh session" fallback, so future maintainers do not add re-raises by accident.
