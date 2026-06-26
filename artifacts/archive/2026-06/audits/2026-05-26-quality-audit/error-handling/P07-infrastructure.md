### Summary

- **Silent data loss in TurnStore**: four session/turn mutation methods wrap transactions in `except Exception: log.exception(); return` with no re-raise, causing write failures to disappear.
- **Zero exception chaining**: no `raise ... from e` found in any of the 42 files audited; original tracebacks are systematically discarded at translation boundaries.
- **WorkerPoolClient retry lacks backoff**: `request_with_routing()` iterates scored workers sequentially with no delay or jitter between attempts, hammering the pool under transient failures.

### Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `src/lyra/infrastructure/stores/turn_store.py` | 213 | **High** | `_log_turn()` catches `Exception` around `db.execute`+`commit`; on failure logs and swallows. The turn is never persisted and the caller is never informed. | Re-raise or return a Result; do not silently drop turn writes. |
| `src/lyra/infrastructure/stores/turn_store_session.py` | 77 | **High** | `_set_cli_session()` catches `Exception`, logs, and returns. Transaction is lost silently. | Re-raise after rollback, or propagate a Result to the caller. |
| `src/lyra/infrastructure/stores/turn_store_session.py` | 126 | **High** | `_increment_resume_count()` catches `Exception`, logs, and returns. Resume count update is lost silently. | Re-raise after rollback, or propagate a Result. |
| `src/lyra/infrastructure/stores/turn_store_session.py` | 143 | **High** | `_end_session()` catches `Exception`, logs, and returns. Session never ends; caller unaware. | Re-raise after rollback, or propagate a Result. |
| `src/lyra/infrastructure/stores/pairing.py` | 207 | **High** | `validate_code()` deletes the pairing code in the IMMEDIATE transaction, then calls `auth_store.upsert()` outside the transaction. If upsert fails, the code is consumed but the grant is not persisted. User gets "Internal error persisting grant." and cannot retry. | Move grant upsert into the IMMEDIATE transaction, or implement compensating undo of the code deletion on failure. |
| `src/lyra/infrastructure/stores/turn_store_queries.py` | 221 | **Medium** | `backfill_sessions()` catches `Exception`, logs, and swallows. One-time backfill failure is silently ignored, leaving `pool_sessions` empty. | Re-raise; backfill failure is startup-fatal. |
| `src/lyra/infrastructure/turn_writer/writer.py` | 132 | **Medium** | `_consume_loop()` catches bare `Exception` per-message. Logs + `nak()` is intentional for JetStream redelivery, but the broad catch masks programming errors (`NameError`, `TypeError`, `AttributeError`) as retryable operational failures. | Narrow to `ValidationError`, `sqlite3.Error`, `nats.errors.Error`, plus an explicit `except Exception: log.critical(); raise` sentinel for unexpected bugs. |
| `src/lyra/nats/nats_bus.py` | 235 | **Medium** | `_handle_nats_message()` catches bare `Exception` on JSON parse. Logs and drops the message permanently with no redelivery or dead-letter. | Narrow to `json.JSONDecodeError`, `UnicodeDecodeError`; consider nak for unexpected errors so JetStream can retry or dead-letter. |
| `src/lyra/nats/nats_bus.py` | 263 | **Medium** | Same handler catches bare `Exception` on `deserialize_dict`. Logs and drops. | Narrow to known deserialization errors; unexpected errors should nak or dead-letter. |
| `src/lyra/transport/worker_pool_client.py` | 159-180 | **Medium** | `request_with_routing()` iterates workers in a tight loop with no sleep/backoff between attempts. Under timeout/no_responders cascades, this immediately exhausts the worker list and trips the circuit breaker. | Add `asyncio.sleep(min(2**attempt * 0.1, 1.0))` or use `tenacity`/`backoff` between worker attempts. |
| `src/lyra/transport/typing_publisher.py` | 55 | **Low** | `except Exception as exc:` logs `str(exc)` via `%s` interpolation. While not user-visible, it leaks raw exception text into logs. | Log `type(exc).__name__` only, matching the `SanitizedError` boundary convention. |
| `src/lyra/infrastructure/turn_writer/health.py` | 107 | **Low** | `_check_nats_connected()` catches bare `Exception` and returns `False`. Defensive probe pattern is acceptable, but masks NATS client bugs as "disconnected". | Acceptable as-is; consider narrowing to `AttributeError` only if `nc.is_connected` is the sole expected failure mode. |

### Metrics

| Metric | Count |
|--------|-------|
| Files audited | 42 |
| `try/except` blocks | 47 |
| `except Exception:` (broad) | 18 |
| `except BaseException:` | 1 |
| Swallowed without re-raise | 7 |
| Proper catch + rollback + re-raise | 3 |
| `raise ... from e` | **0** |
| `str(exc)` in log/user message | 1 |
| Retry loops without backoff | 1 |

### Recommendations (prioritized)

1. **Fix silent swallow in TurnStore mutations** — Change `turn_store.py:_log_turn` and `turn_store_session.py:_set_cli_session`, `_increment_resume_count`, `_end_session` to either re-raise after rollback or return a `Result`. Silent data loss at the L1 memory layer is the highest-impact issue.

2. **Fix PairingManager code consumption gap** — Move `auth_store.upsert` inside the `BEGIN IMMEDIATE ... COMMIT` block in `pairing.py:validate_code()`, or add a compensating delete that restores the code if upsert fails. A consumed code with no persisted grant is a UX dead-end.

3. **Narrow broad catches in NatsBus and TurnWriter** — Replace bare `except Exception:` in `nats_bus.py:_handle_nats_message` and `turn_writer.py:_consume_loop` with specific exception lists plus a final `except Exception: log.critical(); raise` to prevent programming errors from being treated as retryable operational failures.

4. **Add backoff to WorkerPoolClient routing** — Insert an `asyncio.sleep` with exponential backoff capped at ~1s between worker attempts in `worker_pool_client.py:request_with_routing()`. Prevents thundering-herd under transient worker unavailability.

5. **Introduce `raise ... from e` at translation boundaries** — Wherever an exception is caught and replaced (e.g., store query functions that return default values, codec decode paths that return error objects), chain with `raise NewExc(...) from exc` to preserve traceback context for production debugging. This is a codebase-wide convention gap, not limited to P07.
