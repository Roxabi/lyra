# Async Patterns Analysis: Adapters

### Summary
The adapters codebase demonstrates mature async patterns with proper use of `asyncio.Queue`, `asyncio.sleep`, and appropriate exception handling for most I/O operations. However, several areas warrant attention: potential race conditions on shared state dictionaries without synchronization, overly broad exception handling that may mask errors, and a few synchronous file operations within async functions that could block the event loop under heavy load.

### Findings

| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|
| discord/voice/discord_voice.py | 199, 218, 257 | Race condition: `_sessions` dict modified without synchronization | Medium | Consider using `asyncio.Lock` for session state mutations, or document single-threaded assumption |
| nats/nats_envelope_handlers.py | 195 | Blocking I/O: `json.loads()` in async handler can block on large messages | Low | For large payloads, consider running json parsing in executor |
| telegram/telegram_audio.py | 68 | Blocking I/O: `Path.stat()` in async context | Low | Use `asyncio.to_thread()` for file stats on hot paths |
| telegram/telegram.py | 115 | Blocking I/O: `Path.is_dir()` in `__init__` (sync, but called during async setup) | Low | Pre-validate in startup or use async check |
| shared/_shared_streaming_emitter.py | 94, 108, 173 | Over-broad exception handling: `except Exception:` swallows all errors | Medium | Narrow to specific exception types where possible |
| discord/discord_inbound.py | 83, 121, 140, 169, 183, 203 | Over-broad exception handling with silent returns | Medium | Add metrics/counters for dropped errors, narrow exception types |
| nats/nats_outbound_listener.py | 147-171 | Multiple dict `.pop()` operations in finally block without atomicity | Low | Document that cleanup is best-effort, or use single state container |
| shared/_shared.py | 183-185 | Race condition: `TypingTaskManager` dict access without lock | Low | In practice safe due to single-threaded async, but document assumption |
| shared/_inbound_cache.py | 43-46 | Race condition: Cache eviction with multiple dict ops non-atomic | Low | In practice safe, document single-threaded assumption |
| telegram/telegram_outbound.py | 82 | `asyncio.Event()` created but wait pattern may miss signal in edge cases | Low | Current pattern is correct; add comment explaining the design |
| discord/discord_outbound.py | 60, 84 | Fixed sleep intervals: `asyncio.sleep(1.0 * (2**_attempt))` and `asyncio.sleep(9)` | Info | Consider configurable intervals via constants |
| discord/discord_audio_outbound.py | 97-113 | Missing exception context: voice message failure falls back silently | Low | Consider metrics for fallback rate |

### Metrics
- Async functions: 52
- Blocking calls in async: 4 (json.loads, Path.stat, Path.is_dir)
- Potential race conditions: 5 (dict mutations without locks)
- Over-broad exception handlers: 23
- Resource cleanup patterns: 8 (finally blocks)

### Recommendations

1. **High Priority**: Add `asyncio.Lock` protection around `VoiceSessionManager._sessions` mutations. Voice sessions can be manipulated from multiple async tasks (join, leave, stream, invalidate) and concurrent access could corrupt state.

2. **High Priority**: Narrow exception handlers from bare `except Exception:` to specific exception types where feasible. At minimum, add metrics/counters to track which error types are being caught and suppressed.

3. **Medium Priority**: Document the single-threaded async assumption for dictionary mutations in `TypingTaskManager`, `InboundCache`, and stream state management. If the codebase ever introduces multi-threaded execution, these will need synchronization.

4. **Medium Priority**: For the NATS `json.loads()` path, consider adding a size check before parsing and using `asyncio.to_thread()` for messages exceeding a threshold (e.g., >100KB).

5. **Low Priority**: Extract magic numbers for sleep intervals (`9` seconds for Discord typing, `3` seconds for Telegram typing, `120` seconds for chunk timeout) into named constants for configurability.

6. **Low Priority**: Add instrumentation to streaming fallback paths (`_drain_fallback`, `render_audio` fallback to file attachment) to track failure rates.

### Positive patterns observed:
- Consistent use of `asyncio.sleep()` instead of `time.sleep()` throughout
- Proper `CancelledError` handling in worker loops (discord_outbound.py, telegram_outbound.py)
- `finally` blocks for resource cleanup (temp files, typing cancellation)
- `asyncio.gather(*tasks, return_exceptions=True)` for safe task cleanup
- `asyncio.wait_for()` with timeout for bounded waits on queue operations
- `@asynccontextmanager` for typing indicator lifecycle management
