# Error Handling Audit — Partition P10: packages/**/*.py

**Date:** 2026-05-26
**Scope:** roxabi-contracts, roxabi-nats, roxabi-blobs (source only, tests excluded)
**Context:** First full error-handling review of packages (prior 2026-05-18 audit left packages unaudited). Epic #1277 stage-axis refactor active.

---

## Summary

- **roxabi-nats** carries the bulk of findings: broad `except Exception:` swallow in `readiness.py` masks KV-setup failures; `adapter_base.py` silently drops malformed NATS payloads without replying, causing caller timeouts.
- **roxabi-blobs** is comparatively clean: `fs_store.py` chains exceptions correctly and swallows only documented, bounded cases (`FileExistsError` race, `OSError` path escape).
- **roxabi-contracts** has almost no runtime error paths (pure Pydantic schemas); the only concern is a `str(exc)` leak in `connect.py` fallback paths.

---

## Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `roxabi_nats/readiness.py` | 80 | **High** | `except Exception:` swallows all unexpected errors during `lyra-state` KV setup; logs with `log.exception` but returns silently, masking JetStream misconfiguration or auth failures. | Narrow to specific expected exception types (`ServiceUnavailableError`, `BucketNotFoundError`, `ConnectionError`). Let unknown exceptions propagate so they surface in health checks. |
| `roxabi_nats/adapter_base.py` | 212 | **High** | `_dispatch` catches `JSONDecodeError`/`ValueError` and returns without sending a NATS reply. Request-reply callers timeout instead of receiving a structured error. | Reply with a `WorkerError(code="transport.parse", ...)` on malformed JSON so callers get immediate feedback instead of waiting for NATS timeout. |
| `roxabi_nats/readiness.py` | 139 | **Medium** | `except Exception:` in `_kv_watch_for_ready` logs and returns `False`, swallowing watcher-level errors (e.g., JetStream consumer reset). | Narrow exception scope. Consider whether `ConnectionError` or `nats.errors.Error` should propagate rather than being treated as "hub not ready." |
| `roxabi_nats/connect.py` | 55, 98, 110 | **Medium** | `OSError`/`SSLError` caught and forwarded to `sys.exit(...)` without `raise ... from exc` chaining. Original traceback is lost in process-exit logs. | Replace `sys.exit(msg)` with `raise SystemExit(msg) from exc` to preserve the causal chain in crash logs. |
| `roxabi_nats/connect.py` | 61, 104 | **Medium** | `str(exc)` used as fallback when `exc.strerror` is `None`. Raw `str(OSError)` can leak file paths or OS internals into the process exit message. | Use a static fallback like `"unreadable"` instead of `str(exc)`; the path is already in the message. |
| `roxabi_nats/readiness.py` | 146-162 | **Low** | `_open_kv_with_retry` uses fixed `asyncio.sleep(0.5)` with no exponential backoff, jitter, or attempt cap. Could hammer NATS if bucket creation is stalled. | Add exponential backoff (max 4s) and a hard attempt limit (e.g., 60) so the loop degrades gracefully under persistent `BucketNotFoundError`. |
| `roxabi_blobs/http_store.py` | 62 | **Low** | Test-seam `_start_asgi_lifespan` swallows `except Exception: pass` (BLE001 noqa). ASGI app crashes are silently ignored. | At minimum log the exception at DEBUG so test failures are traceable. |
| `roxabi_nats/testing/voice.py` | 102, 215 | **Low** | `FakeTtsWorker.start` / `FakeSttWorker.start` use `except Exception:` to cleanup on subscribe failure, then `raise`. Broad catch risks masking `CancelledError` during asyncio shutdown. | Narrow to `nats.errors.Error` and `OSError`; let `CancelledError`/`KeyboardInterrupt` propagate natively. |
| `roxabi_nats/testing/image.py` | 99 | **Low** | Same pattern as voice test doubles: `except Exception:` around subscribe with cleanup + re-raise. | Narrow to `nats.errors.Error` and `OSError`. |
| `roxabi_nats/adapter_base.py` | 266 | **Low** | Heartbeat publish catches `nats.errors.Error` broadly and logs with `exc_info=True`. No backpressure or circuit breaker on repeated publish failures. | Acceptable as-is for heartbeat (best-effort), but document why circuit breaker is intentionally omitted. |

---

## Metrics

| Metric | Count |
|--------|-------|
| Source files audited | 64 |
| `except Exception:` or bare `except:` (source) | 5 |
| Swallowed exceptions (no re-raise, no reply) | 4 |
| `str(exc)` into user-visible / exit messages | 2 |
| Missing `raise ... from e` chains | 3 |
| Retry loops without backoff | 1 |
| Correct `raise ... from e` chains found | 6 |
| **Severity distribution** | High 2 / Medium 4 / Low 4 |

---

## Recommendations (prioritized)

1. **Reply on malformed JSON in `adapter_base._dispatch`** — Add a `self.reply(msg, error_json)` path when `json.loads` or envelope validation fails. This closes a silent-timeout DoS vector where a single bad payload causes every caller to wait for NATS `timeout` seconds.

2. **Narrow `except Exception:` in `readiness.py`** — Replace the two broad catches (lines 80, 139) with explicit exception types. Unexpected errors should crash the adapter so they are visible in container restart loops and health checks.

3. **Chain exceptions in `connect.py` sys.exit paths** — Use `raise SystemExit(msg) from exc` for `OSError` and `SSLError` so that `__cause__` is available in Sentry/crash dumps.

4. **Add backoff to `_open_kv_with_retry`** — Change fixed `sleep(0.5)` to exponential backoff (0.5, 1, 2, 4) with jitter. The current fixed-rate loop is harmless at low scale but becomes a thundering-herd risk if many adapters restart simultaneously.

5. **Document the heartbeat no-circuit-breaker decision** — `adapter_base.py` heartbeat publish intentionally lacks circuit breaking. Add an inline comment referencing `NatsCircuitBreaker` and explaining why heartbeat differs from RPC paths (heartbeat is best-effort telemetry, not a user request).
