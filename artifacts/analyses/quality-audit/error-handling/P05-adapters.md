# Error Handling Analysis: Adapters

### Summary

The adapters area has 74 try/except blocks across 37 Python files. Error handling is generally functional with good use of logging, but exhibits several code quality issues: 39 generic `Exception` catches (many without exception context logging), no bare `except:` clauses (good), and inconsistent patterns for preserving error context. The codebase follows defensive programming patterns suitable for networked services but could improve error visibility and specificity.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| /home/mickael/projects/lyra/src/lyra/adapters/nats/nats_envelope_handlers.py | 55, 83, 107, 142, 196 | Generic Exception catch with log.warning missing exc_info=True | Medium | Add exc_info=True or use log.exception to preserve stack trace context |
| /home/mickael/projects/lyra/src/lyra/adapters/nats/nats_stream_decoder.py | 144 | Generic Exception catch in reaper loop - acceptable but could be more specific | Low | Consider catching specific transient exceptions |
| /home/mickael/projects/lyra/src/lyra/adapters/nats/nats_outbound_listener.py | 124, 161 | Generic Exception catch with logging but no exception variable captured | Medium | Use `except Exception as e:` and include e in log message |
| /home/mickael/projects/lyra/src/lyra/adapters/discord/discord_audio.py | 148, 157, 178, 208 | Generic Exception catches for audio operations - missing exception context in logs | Medium | Add exc_info=True to log.warning calls or use log.exception |
| /home/mickael/projects/lyra/src/lyra/adapters/discord/lifecycle.py | 44, 51, 67 | Generic Exception catches - good use of log.exception at 45 but inconsistent at 51, 67 | Low | Use log.exception consistently instead of log.warning for lifecycle errors |
| /home/mickael/projects/lyra/src/lyra/adapters/discord/discord_inbound.py | 83, 121, 169, 183, 203 | Multiple generic Exception catches - some use log.exception, others log.warning | Medium | Standardize to log.exception for error context preservation |
| /home/mickael/projects/lyra/src/lyra/adapters/discord/discord_threads.py | 36, 78 | Generic Exception catch with log.exception - acceptable pattern | Low | Good pattern, consider more specific exception types if feasible |
| /home/mickael/projects/lyra/src/lyra/adapters/discord/discord_outbound.py | 227 | Generic Exception catch for send failure - logged but exception context lost | Medium | Use log.exception instead of log.exception (already good) |
| /home/mickael/projects/lyra/src/lyra/adapters/discord/discord_audio_outbound.py | 105, 159 | Generic Exception catches with log.warning(exc_info=True) - acceptable | Low | Pattern is acceptable, could be more specific |
| /home/mickael/projects/lyra/src/lyra/adapters/telegram/telegram_audio.py | 75 | Generic Exception catch for download cleanup - exception re-raised after cleanup | Low | Good pattern with raise, acceptable |
| /home/mickael/projects/lyra/src/lyra/adapters/telegram/telegram_inbound.py | 82, 170, 176 | Generic Exception catches - some missing exception context | Medium | Use log.exception consistently |
| /home/mickael/projects/lyra/src/lyra/adapters/telegram/telegram_outbound.py | 242 | Generic Exception catch for send failure - logged but no exception variable | Medium | Use `except Exception as e:` and include in log |
| /home/mickael/projects/lyra/src/lyra/adapters/shared/_shared.py | 239 | Generic Exception catch in retry logic - intentionally swallows after max attempts | Low | Documented intentional swallowing, acceptable |
| /home/mickael/projects/lyra/src/lyra/adapters/shared/_shared_streaming_emitter.py | 94, 108, 186, 202, 207 | Multiple Exception catches in streaming - some use log.debug to skip edits | Medium | Add exc_info to log.debug where error might be significant |
| /home/mickael/projects/lyra/src/lyra/adapters/shared/_inbound_cache.py | 95 | Generic Exception catch for deserialization - logged but no stack trace | Medium | Add exc_info=True for debugging failed deserialization |
| /home/mickael/projects/lyra/src/lyra/adapters/discord/adapter.py | 153 | Generic Exception catch for ThreadStore close - uses log.exception | Low | Good pattern |

### Metrics

- Total try/except blocks: 74
- Bare excepts: 0
- Generic Exception catches: 39
- Swallowed exceptions (log + return/continue): 28
- Proper re-raises: 4
- finally blocks: 5
- log.exception uses: 21
- log.warning without exc_info: 52

### Recommendations

1. **High Priority**: Add `exc_info=True` to all `log.warning` calls in exception handlers to preserve stack trace context for debugging. Affects nats_envelope_handlers.py, discord_audio.py, telegram_inbound.py, and shared/_inbound_cache.py.

2. **High Priority**: Standardize exception capture syntax - use `except Exception as e:` consistently and reference the exception variable in log messages for better error context.

3. **Medium Priority**: Consider defining adapter-specific exception classes (e.g., `DeserializationError`, `PlatformAPIError`) to replace generic Exception catches where feasible, improving error specificity.

4. **Medium Priority**: Document intentional exception swallowing patterns (like `send_with_retry`) with clear comments explaining why exceptions are intentionally caught and logged without re-raising.

5. **Low Priority**: Review streaming emitter exception handlers in `_shared_streaming_emitter.py` - several use `log.debug` for skipped edits which may hide genuine issues during high-volume streaming.

6. **Low Priority**: Add context managers (asynccontextmanager) for resource cleanup patterns that currently rely on finally blocks, improving readability.
