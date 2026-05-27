### Summary
- One high-severity resource leak: `OutboundEmitter.run()` abandons the `events` async iterator when the initial `__anext__()` raises, leaving NATS queue consumers or file descriptors unclosed.
- One medium-severity latent blocking risk: `PlatformCallbacks.start_typing` / `cancel_typing` are typed as sync `Callable[[], None]` and called without `await` in the legacy path.
- No race conditions or un-awaited coroutines found inside the partition; the remaining items are low-severity boundary concerns.

### Findings
| File | Line | Severity | Description | Recommendation |
|---|---|---|---|---|
| `src/lyra/outbound/emitter.py` | 98-101 | Medium | `PlatformCallbacks.start_typing` and `cancel_typing` are typed as `Callable[[], None]` (sync). In `_start_typing` / `_cancel_typing` (lines 287-299) the legacy `else` branch calls them directly without `await`. Current adapter implementations are non-blocking (they only mutate an `asyncio.Task` dict), but the type contract permits blocking callables. | Change the signature to `Callable[[], Awaitable[None]]` and await both branches, or add a runtime `asyncio.iscoroutinefunction` guard. Document the non-blocking invariant in `outbound/CLAUDE.md` if the sync shape is intentional. |
| `src/lyra/outbound/emitter.py` | 500-518 | High | `run()` manually calls `await events.__anext__()` to peek the first event. If that call raises (`peek_error`), the method re-raises without ever closing the `events` async iterator (no `await events.aclose()` and no `try/finally` block). When `events` is `decode_stream_events()` backed by a NATS queue consumer, the queue and its subscription remain alive until GC, which may never run promptly. | Replace the manual peek with a `try/finally` that guarantees `await events.aclose()` on every non-success path, or restructure the peek as an `async for` over a wrapper that yields a sentinel on empty/error so Python's native `async for` cleanup fires. |
| `src/lyra/outbound/emitter.py` | 320-335 | Low | `_drain_fallback()` does `async for event in events` without a `try/finally`. If `events.__anext__()` raises mid-drain, the exception propagates and the caller (`run()`) returns early, but `_drain_fallback` itself does not log how far it got. | Add a `try/except Exception` around the `async for` to log the partial drain length before re-raising, or document that partial drain is acceptable. |
| `src/lyra/adapters/shared/_tool_recap.py` | 118-121 | Low | `ToolRecapAccumulator.observe_end()` calls `json.loads(partial.args_buffer)` synchronously. It is invoked from async `_on_toolcall_v2` in `emitter.py`. Tool args are typically small, but large or deeply nested payloads will block the event loop. | Add a size guard (e.g., >1 KB) and offload to `loop.run_in_executor` for oversized buffers, or switch to `orjson` if available. |
| `src/lyra/streaming/state_machine.py` | 36-73 | Low | `StateMachine` and `EventEmitter` hold mutable in-memory state (`open_blocks`, `pending`, `dedup_seen`) with no explicit `clear()` or `reset()` method. Today they are per-parser and parser lifetimes are short, but a cancelled or abandoned stream leaves the state simply dropped. | Add `reset()` to `StateMachine` and `EventEmitter` that clears all buckets, and ensure upstream callers (parser implementations) invoke it in a `finally` block when the stream ends or is cancelled. |

### Metrics
| Metric | Count |
|---|---|
| Files analyzed | 7 (outbound: 5 + streaming: 2, `__init__.py` excluded) |
| Async functions / coroutines | 17 |
| Blocking calls found | 2 (sync callback invocation, `json.loads` via adapter) |
| Resource leaks | 1 (async iterator on `peek_error` path) |
| Un-awaited coroutines | 0 |
| Race conditions in shared state | 0 |
| Async generators without explicit `aclose` | 1 |

### Recommendations (prioritized)
1. **Fix `peek_error` async iterator leak in `OutboundEmitter.run()`** — Add `try/finally` around the manual `__anext__` call or use a sentinel-based `async for` wrapper so the iterator is always closed on early exit. This is the only finding that can leak OS resources in production.
2. **Strengthen `PlatformCallbacks` typing for typing indicators** — Convert `start_typing` / `cancel_typing` to `Callable[[], Awaitable[None]]` and await them uniformly. If backwards compatibility with the transitional `ThrottleCapability` path requires sync shapes, split into two Protocol slots or add an `asyncio.iscoroutinefunction` branch.
3. **Guard `json.loads` in `_tool_recap.py`** — Offload to a thread executor when `len(partial.args_buffer)` exceeds a conservative threshold (1 KB) to prevent event-loop stalls from adversarial or buggy tool outputs.
4. **Add `reset()` to `StateMachine` / `EventEmitter` and wire into parser cleanup** — Keeps the streaming primitives robust for future reuse scenarios (e.g., recycled parser instances) and makes cancellation semantics explicit.
5. **Audit upstream `send_streaming` callers** — Verify that `NatsOutboundListener._drain_stream`, `StreamingDispatch.dispatch`, and `clipool_worker` wrap `await adapter.send_streaming()` in `try/finally` that drains or aborts the `chunks` iterator if `OutboundEmitter.run()` raises. This closes the leak boundary even if item 1 is not yet fixed.
