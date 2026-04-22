# Error Handling Analysis: Bootstrap

### Summary

The bootstrap area demonstrates reasonably mature error handling with no bare `except:` clauses and good use of finally blocks for resource cleanup. However, there are several areas of concern: widespread generic `Exception` catches (14 instances) that may mask underlying issues, two intentionally swallowed exceptions (both justified), and inconsistent error propagation strategies (mix of sys.exit, raise, and log-only approaches).

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| /home/mickael/projects/lyra/src/lyra/bootstrap/infra/health.py | 36 | Generic Exception catch for `nc.is_connected` | Low | Acceptable - defensive probe with log.debug context |
| /home/mickael/projects/lyra/src/lyra/bootstrap/infra/embedded_nats.py | 181 | Generic Exception catch for NATS connect | Medium | Narrow to expected connection errors (OSError, TimeoutError) |
| /home/mickael/projects/lyra/src/lyra/bootstrap/infra/notify.py | 35 | Generic Exception for Telegram notification | Low | Acceptable - best-effort notification with logging |
| /home/mickael/projects/lyra/src/lyra/bootstrap/factory/bot_agent_map.py | 78 | Generic Exception with noqa: BLE001 | Medium | Catch specific DB errors or re-raise with context |
| /home/mickael/projects/lyra/src/lyra/bootstrap/factory/voice_overlay.py | 88 | Generic Exception for voice probe | Low | Acceptable - best-effort startup probe |
| /home/mickael/projects/lyra/src/lyra/bootstrap/factory/unified.py | 290 | Generic Exception for NATS close | Low | Acceptable - cleanup on shutdown |
| /home/mickael/projects/lyra/src/lyra/bootstrap/factory/utils.py | 86-87 | Swallowed CancelledError in finally | Low | Acceptable - intentional cleanup pattern |
| /home/mickael/projects/lyra/src/lyra/bootstrap/bootstrap_stores.py | 134-135 | Swallowed OperationalError for index | Low | Acceptable - idempotent migration |
| /home/mickael/projects/lyra/src/lyra/bootstrap/wiring/bootstrap_wiring.py | 221 | Generic Exception re-raised after cleanup | Medium | Narrow to expected wiring errors |
| /home/mickael/projects/lyra/src/lyra/bootstrap/standalone/adapter_standalone.py | 47 | Generic Exception for NATS connect | Medium | Narrow to expected connection errors |
| /home/mickael/projects/lyra/src/lyra/bootstrap/standalone/tts_adapter_standalone.py | 95 | Generic Exception for VRAM probe | Low | Acceptable - optional feature fallback |
| /home/mickael/projects/lyra/src/lyra/bootstrap/standalone/tts_adapter_standalone.py | 150 | Generic Exception for TTS synthesis | Medium | Already handled with log.exception and error response - acceptable |
| /home/mickael/projects/lyra/src/lyra/bootstrap/standalone/hub_standalone.py | 69 | Generic Exception for NATS connect | Medium | Narrow to expected connection errors |
| /home/mickael/projects/lyra/src/lyra/bootstrap/standalone/hub_standalone.py | 254 | Generic Exception for NATS close | Low | Acceptable - cleanup on shutdown |
| /home/mickael/projects/lyra/src/lyra/bootstrap/standalone/stt_adapter_standalone.py | 68 | Generic Exception for VRAM probe | Low | Acceptable - optional feature fallback |
| /home/mickael/projects/lyra/src/lyra/bootstrap/standalone/stt_adapter_standalone.py | 126 | Generic Exception for STT transcription | Medium | Already handled with log.exception and error response - acceptable |
| /home/mickael/projects/lyra/src/lyra/bootstrap/standalone/adapter_standalone.py | 210-212 | Missing error context for channel ID parsing | Low | Log includes value and bot_id - adequate |

### Metrics

- Try/except blocks: 47
- Bare excepts: 0
- Swallowed exceptions: 2 (both intentional with justification)
- Generic Exception catches: 14
- Finally blocks: 13

### Recommendations

1. **HIGH PRIORITY**: Narrow NATS connection exception handlers in `embedded_nats.py:181`, `adapter_standalone.py:47`, and `hub_standalone.py:69` to catch specific connection errors (OSError, TimeoutError, connection-related exceptions) rather than blanket Exception.

2. **MEDIUM PRIORITY**: Replace generic Exception catch in `bot_agent_map.py:78` with specific database exceptions (sqlite3.Error, or the store's custom exceptions if available). The noqa: BLE001 comment indicates awareness but not resolution.

3. **MEDIUM PRIORITY**: Add explicit exception type narrowing for `bootstrap_wiring.py:221` - the re-raise pattern is good but the catch scope is too broad.

4. **LOW PRIORITY**: Consider standardizing error propagation strategy across bootstrap - currently mixes sys.exit(), raise, and log-only. Document the intended pattern for each module type (infra helpers vs standalone entry points vs wiring).

5. **LOW PRIORITY**: The swallowed exceptions in `utils.py:86` and `bootstrap_stores.py:134` are justified (cleanup cancellation handling and idempotent index creation). Add inline comments documenting the intentional nature of these passes.
