### Summary

- 2 user-visible `str(exc)` leaks in `/config` and `/config reset` commands
- 0 explicit exception chaining (`raise ... from`) across 93 files / 73 raise statements
- 11 unmarked broad-catch boundaries (`except Exception` without `DEBT:` or re-raise)

### Findings

| File | Line | Severity | Description | Recommendation |
|---|---|---|---|---|
| `commands/builtin_commands.py` | 169 | High | `str(exc)` leaked into user-visible `Response` for `/config` command | Use `safe_error_response()` or `type(exc).__name__` |
| `commands/builtin_commands.py` | 202 | High | `str(exc)` leaked into user-visible `Response` for `/config reset` | Same as above |
| `auth/authenticator.py` | 190 | Medium | `except ValueError: raise ValueError(...)` loses original cause | Add `raise ... from` chain |
| `runtime_config.py` | 195 | Medium | `except (ValueError, TypeError): raise ValueError(...)` for `temperature` loses cause | Add `raise ... from` chain |
| `runtime_config.py` | 206 | Medium | Same pattern for `max_steps` conversion | Add `raise ... from` chain |
| `runtime_config.py` | 236 | Medium | Same pattern for `debounce_ms` conversion | Add `raise ... from` chain |
| `pool/pool_observer.py` | 97,135,150,172,206 | Low | 5 `except Exception:` swallowing blocks in observability callbacks, all unmarked | Add `DEBT:boundary-broad-catch` markers or narrow types |
| `pool/pool_processor.py` | 52 | Low | `except Exception:` swallows session resume failure without marker | Add marker or narrow to expected types |
| `pool/pool_processor_exec.py` | 82 | Low | `except Exception as exc:` swallows unhandled agent errors without marker | Add `DEBT:boundary-broad-catch` marker |
| `pool/pool_processor_exec.py` | 273 | Low | `_safe_dispatch` swallows all dispatch errors without marker | Add marker or propagate after logging |
| `cli/cli_pool.py` | 212 | Low | `except Exception as exc:` swallows send failures, returns generic error | Add marker or narrow types |
| `cli/cli_non_streaming.py` | 202 | Low | `except Exception as exc:` swallows read errors, returns generic error | Add marker or narrow types |
| `cli/cli_pool_worker.py` | 321 | Low | `except Exception:` swallows `on_reap` callback failures without marker | Add marker or narrow types |
| `commands/builtin_commands.py` | 195 | Low | `except OSError: pass` when unlinking runtime file (redundant: `missing_ok=True` already set) | Remove the try/except; `missing_ok=True` is sufficient |

### Metrics

| Metric | Count |
|---|---|
| Files analyzed | 93 |
| `try` blocks | 95 |
| `except` clauses | 98 |
| `raise` statements | 73 |
| `raise ... from` (explicit chaining) | **0** |
| `except Exception` (broad catch) | 37 |
| Marked with `DEBT:boundary-broad-catch` | 23 |
| **Unmarked broad catch** | **14** |
| `str(exc)` in user-facing messages | 2 |
| `except ...: pass` (empty swallow) | 8 |
| `except ...: continue` (loop swallow) | 2 |

### Recommendations

1. **Fix `str(exc)` leaks** — Replace the two `Response(content=str(exc))` returns in `builtin_commands.py` with `safe_error_response()` or a sanitized message. This directly addresses the #1212 cascade pattern.
2. **Add exception chaining** — Introduce `raise ... from` at the 4 value-conversion sites in `runtime_config.py` and `authenticator.py`. These are the only wrapping-raise sites in the partition and all currently lose the original traceback.
3. **Mark unmarked boundaries** — Add `# noqa: BLE001 — DEBT:boundary-broad-catch` markers to the 11 unmarked `except Exception` blocks (pool_observer, pool_processor, pool_processor_exec, cli_pool, cli_non_streaming, cli_pool_worker). This makes the resilience contract explicit and greppable.
4. **Narrow pool_observer catches** — The 5 `except Exception` blocks in `PoolObserver` are all infrastructure calls (TurnPublisher, MessageIndex, turn_logger, session_update). They should catch specific transport/store exceptions (e.g., `NatsError`, `TurnStoreError`) rather than `Exception` to avoid masking `KeyboardInterrupt`/`SystemExit` bugs.
5. **Standardize boundary pattern** — The copy-pasted `except Exception: log...` resilience blocks across the partition (23+ instances) are a maintenance burden. Consider a `@resilient` decorator or a `swallow_exc()` helper to centralize logging, circuit-breaker recording, and marker conventions.
