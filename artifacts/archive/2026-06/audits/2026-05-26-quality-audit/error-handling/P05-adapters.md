### Summary

- **21 `except Exception` blocks (32 % of all except blocks)** in 36 files; zero bare `except:`.
- **#1212 `str(exc)` cascade is eradicated** in this partition: no `str(exc)` reaches user-visible messages. Type-name sanitization (`type(exc).__name__`) is the consistent pattern.
- **One high-severity finding**: `NatsOutboundListener._drain_stream` swallows all `send_streaming` failures at the top boundary, leaving users with silent stream drops and no circuit-breaker recording.

### Findings

| File | Line | Severity | Description | Recommendation |
|---|---|---|---|---|
| `src/lyra/adapters/nats/nats_outbound_listener.py` | 166 | **High** | `_drain_stream` wraps `adapter.send_streaming()` in `except Exception` and only logs. Users see silent stream aborts; circuit breaker never records the failure. | Propagate a structured `StreamDeliveryError` to the caller, or at minimum record the failure on the circuit registry before swallowing. |
| `src/lyra/adapters/discord/discord_outbound.py` | 275 | **Medium** | `_send_message` final chunk catches `Exception` with no retry. Intermediate chunks use `send_with_retry`; the terminal chunk silently disappears on failure. | Wrap the final chunk in `send_with_retry` too, or return `last_id` as `None` so the caller can render a delivery-failure notice. |
| `src/lyra/adapters/telegram/telegram_outbound.py` | 266 | **Medium** | `_send_message` final chunk catches `Exception` with no retry, mirroring the Discord gap above. | Same as Discord: apply `send_with_retry` to the final chunk, or surface a delivery-failure notice. |
| `src/lyra/adapters/discord/discord_inbound.py` | 112 | **Medium** | `_try_auto_create_thread` catches `Exception` broadly. Recovery logic exists, but thread-scoped DB errors, rate-limits, and permission denials all hit the same path. | Narrow to `discord.HTTPException` + `RuntimeError`; keep `exc_info` on the fallback log so Sentry tags distinguish error families. |
| `src/lyra/adapters/nats/mint_failure_subscriber.py` | 129 | **Medium** | `_nc.publish` alert send catches `Exception`; ops alerts are silently dropped on NATS failure. | Add a local retry (3x with 1 s backoff) or publish to a dead-letter subject so alerts are not lost. |
| `src/lyra/adapters/clipool/clipool_worker.py` | 332 | **Medium** | `_handle_control` catches `Exception` broadly and returns `ok=False` with no error classification. Unlike `_handle_cmd_streaming`/`_handle_cmd_blocking`, there is no `_classify_exception` call. | Reuse `_classify_exception` in the control path, or at minimum log the exception type and op name. |
| `src/lyra/adapters/shared/_inbound_cache.py` | 95 | **Low** | `deserialize_dict` failure on embedded `original_msg` is swallowed. Cache miss recovery is best-effort, but the failure reason is lost. | Log `type(exc).__name__` alongside the warning so deserialization regressions are observable. |
| `src/lyra/adapters/discord/discord_threads.py` | 39, 83 | **Low** | `persist_thread_claim` and `persist_thread_session` both swallow `Exception`. DB write failures are invisible to the inbound pipeline. | Convert to `except (sqlite3.Error, RuntimeError)` and increment a metric/counter so DB health can be monitored. |
| `src/lyra/adapters/discord/discord_audio.py` | 232 | **Low** | Lazy `is_owned` check on the audio path catches `Exception` broadly. | Narrow to `sqlite3.Error` + `RuntimeError` to match the text-path precedent in `_discord_pre_route_hook`. |
| `src/lyra/adapters/shared/_shared_audio.py` | 74 | **Low** | `buffer_audio_chunks` catches `Exception` broadly during async chunk iteration. The error is stored and later re-raised via `_PartialAudioError`, which is correct, but the log uses `str(exc)`. | Keep the broad catch (stream source is external), but switch log to `type(exc).__name__` for consistency with #1212 hygiene. |
| `src/lyra/adapters/shared/_shared_audio.py` | 95 | **Low** | `_PartialAudioError` is raised without `from stream_error`, losing the original traceback chain. | Change to `raise _PartialAudioError(assembled, stream_error) from stream_error`. |
| `src/lyra/adapters/telegram/telegram_audio.py` | 75 | **Low** | Cleanup `except Exception` on download failure re-raises without chaining the original cause. | Use `except Exception as exc: ... raise exc from exc` (or `raise`) so the temp-file cleanup step is visible in traceback context. |
| `src/lyra/adapters/discord/discord_outbound.py` | 318 | **Low** | `_edit_tool_recap` logs `str(exc)` at debug level. | Switch to `type(exc).__name__` to align with #1212 hygiene (debug logs are still observable in production). |
| `src/lyra/adapters/discord/discord_outbound.py` | 121 | **Low** | `except asyncio.CancelledError: pass` in typing worker — acceptable pattern, but should be `raise` to let `cancel_all()` gather see it. | Replace `pass` with `raise` so `TypingTaskManager.cancel_all()` receives the expected `CancelledError` in `asyncio.gather(return_exceptions=True)`. |

### Metrics

| Metric | Value |
|---|---|
| Files analyzed | 36 |
| `try:` blocks | 65 |
| `except` clauses | 66 |
| `except Exception` (or `except Exception as ...`) | 21 (31.8 %) |
| Bare `except:` | 0 |
| `raise ... from e` chains | 1 (`_shared_audio.py:119`) |
| Empty `pass` in `except` (non-CancelledError) | 0 |
| `str(exc)` in user-visible messages | 0 (#1212 eradicated) |
| Retry-with-backoff implementations | 2 (`send_with_retry`, `_discord_typing_worker` resolve retry) |
| Final-chunk sends without retry | 2 (Discord, Telegram) |

### Recommendations (prioritized)

1. **Fix `NatsOutboundListener._drain_stream` silent swallow (high).** The top-level `except Exception` around `send_streaming` is the single biggest reliability gap. Either propagate a structured error or record the failure on the circuit breaker before logging.
2. **Apply `send_with_retry` to final chunks in both adapters (medium).** The final chunk is currently the only send operation in the outbound path without retry. Unify by wrapping it in the existing retry helper.
3. **Add `_classify_exception` to `clipool_worker._handle_control` (medium).** The control path is the only clipool handler that returns an opaque `ok=False` without a structured `WorkerError`. Align with the cmd handlers.
4. **Narrow broad catches in `discord_threads.py` and `discord_audio.py` (low).** Replace `except Exception` with `except (sqlite3.Error, RuntimeError)` and increment a metric so DB health regressions are observable in dashboards.
5. **Add `from` chaining to `_PartialAudioError` and `telegram_audio.py` cleanup (low).** Two-line fixes that restore traceback fidelity for debugging production audio-path issues.
