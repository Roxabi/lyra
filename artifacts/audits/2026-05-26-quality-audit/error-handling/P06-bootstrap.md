### Summary

- **Unprotected sequential cleanup is the top risk**: `teardown_buses`, `teardown_dispatchers`, `open_stores` finally, and `_atomic_table_copy` finally all use unguarded sequential `await`/`close()` — one failure orphans the rest.
- **11 `except Exception` boundary catches are annotated as debt** (`DEBT:boundary-broad-catch`) but remain live in runtime paths (agent factory, bot-agent map, voice probe, health probe, startup notification) where they can mask coding errors.
- **Missing exception chain**: `wiring_helpers.py` raises `SystemExit(str(exc))` from a `ValueError` without `from exc`, discarding the original traceback.

### Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `src/lyra/bootstrap/lifecycle/lifecycle_helpers.py` | 44-53 | High | `teardown_buses` and `teardown_dispatchers` iterate sequentially with bare `await bus.stop()` / `await d.stop()`; if one raises, remaining resources are never stopped. | Replace loops with `asyncio.gather(..., return_exceptions=True)` or delegate to `close_safely`. |
| `src/lyra/bootstrap/bootstrap_stores.py` | 286-297 | High | `open_stores` finally iterates over `all_stores` with bare `await store.close()`; one failing close skips the rest. | Use `asyncio.gather` with `return_exceptions=True` or `close_safely`. |
| `src/lyra/bootstrap/bootstrap_stores.py` | 154-158 | Medium | `_atomic_table_copy` finally calls `src.close()` then `dst.close()` sequentially; `src.close()` failure skips `dst.close()`. | Wrap each `close()` in its own `try/except` inside the finally block. |
| `src/lyra/bootstrap/factory/wiring_helpers.py` | 138-139 | Medium | `except ValueError as exc: raise SystemExit(str(exc))` discards the original traceback. | `raise SystemExit(str(exc)) from exc` |
| `src/lyra/bootstrap/bootstrap_stores.py` | 143-146 | Low | `sqlite3.OperationalError` swallowed with empty `pass` during index creation; genuine corruption would be silent. | Log the error string at `debug` before passing, or check `"already exists"` in the message. |
| `src/lyra/bootstrap/factory/voice_overlay.py` | 124-130 | Low | `probe_voice_services` catches `Exception` and logs `str(exc)` for any failure, treating coding errors as "probe failed unexpectedly". | Narrow to `nats.errors.Error` / `TimeoutError`; use `log.exception` for unexpected types. |
| `src/lyra/bootstrap/factory/agent_factory.py` | 172-177 | Low | `except Exception` around `SessionTools` construction logs `exc_info=True` but swallows the error, leaving `session_tools=None`. | Acceptable resilience pattern; consider narrowing to `ImportError` / `ConnectionError` if boundary is known. |
| `src/lyra/bootstrap/factory/bot_agent_map.py` | 78-85 | Low | `except Exception` around `agent_store.set_bot_agent()` logs `str(exc)` and continues; DB seed failure is non-fatal. | Acceptable resilience pattern; consider narrowing to `sqlite3.Error`. |
| `src/lyra/bootstrap/standalone/hub_standalone.py` | 295-299 | Low | `except Exception` around `nc.close()` during shutdown logs `str(exc)`; defensive but broad. | Already annotated `DEBT:boundary-broad-catch`; leave for dedicated boundary audit. |
| `src/lyra/bootstrap/unified.py` | 84-88 | Low | `except nats.errors.Error` around `nc.close()` — actually specific, but logs `str(exc)`. | No action required; specific catch is correct. |

### Metrics

| Metric | Count |
|--------|-------|
| Total files analyzed | 30 |
| Files with error-handling concerns | 8 |
| Broad `except Exception` (incl. shutdown & runtime) | 11 |
| Truly bare `except:` | 0 |
| Swallowed exceptions (`pass` without log or re-raise) | 1 |
| Missing `raise ... from e` (traceback loss) | 1 |
| Unprotected sequential cleanup locations | 4 |
| `str(exc)` in operator-facing messages (logs / stderr) | 8 |

### Recommendations

1. **Fix unprotected sequential cleanup (P0)** — Apply `asyncio.gather(..., return_exceptions=True)` or the existing `close_safely` helper to `teardown_buses`, `teardown_dispatchers`, `open_stores` finally, and `_atomic_table_copy` finally. One resource failing to close must not orphan the others.

2. **Preserve exception chains (P1)** — Change `wiring_helpers.py:139` to `raise SystemExit(str(exc)) from exc` so `load_multibot_config` validation tracebacks are not lost.

3. **Distinguish idempotent migration noise from real errors (P2)** — In `bootstrap_stores.py:145`, log the `sqlite3.OperationalError` text at `debug` level before the `pass`, or gate the swallow on `"already exists"` so index corruption doesn't go fully silent.

4. **Narrow voice probe catch scope (P2)** — In `voice_overlay.py:124`, replace `except Exception` with `except (nats.errors.Error, TimeoutError, OSError)` and add a separate `except Exception: log.exception(...)` branch so programming errors surface as crashes rather than "probe failed" warnings.

5. **Audit non-shutdown `except Exception` for type narrowing (P3)** — The runtime broad catches in `agent_factory.py:172` and `bot_agent_map.py:78` are resilient-by-design but mask coding errors. Evaluate whether the actual failure modes are bounded to `ImportError` / `sqlite3.Error` / `ConnectionError` and narrow accordingly.
