# Async Patterns Audit — Partition P05 (adapters)
**Scope:** `src/lyra/adapters/**/*.py`
**Date:** 2026-05-27
**Context:** Epic #1277 stage-axis refactor; prior 2026-05-18 audit covered hexagonal conformance and duplication. This audit focuses on async-specific risks only.

---

## Summary
- **Telegram audio path** carries the highest risk: blocking file I/O (`mkstemp`, `stat`, `read_bytes`, `unlink`) runs directly on the event loop, and the `mkstemp` fd is discarded and never closed, leaking descriptors per voice message.
- **NATS stream teardown** leaves cancelled drain tasks un-awaited in `NatsOutboundListener.stop()`, and **shared `_shared_audio.py`** does not explicitly `aclose()` the async iterator on stream interruption, creating dangling generator / queue state.
- **No critical race conditions** were found in shared adapter state (`_owned_threads`, `_thread_sessions`); all mutations are single-event-loop atomic, though the by-reference sharing pattern is fragile if the pipeline ever yields inside critical sections.

---

## Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|---------------|
| `src/lyra/adapters/telegram/telegram_audio.py` | 63 | Medium | `tempfile.mkstemp()` fd is discarded (`_`) and never closed; it is also a blocking syscall inside an async method. | Close fd immediately with `os.close(fd)`, or switch to `asyncio.to_thread(tempfile.mkstemp)` and close fd. |
| `src/lyra/adapters/telegram/telegram_audio.py` | 68, 163, 165 | Medium | Blocking sync file I/O (`Path.stat().st_size`, `Path.read_bytes()`, `Path.unlink()`) in async `_download_audio` / `render_audio`. | Wrap in `asyncio.to_thread()` or use `aiofiles` for all filesystem access on the event loop. |
| `src/lyra/adapters/telegram/telegram.py` | 111–119 | Low | Blocking filesystem checks (`Path.is_dir()`, `os.access()`) in `TelegramAdapter.__init__`. | Move validation to an async `setup()` coroutine, or wrap checks in `asyncio.to_thread()`. |
| `src/lyra/adapters/telegram/telegram.py` | 218–221 | Medium | `TelegramAdapter.close()` stops typing and the outbound listener, but never closes the aiogram `Bot` session, leaking the underlying aiohttp connection pool. | Add `await self._bot.session.close()` (or equivalent aiogram v3 teardown) in `close()`. |
| `src/lyra/adapters/shared/_shared_audio.py` | 60–74 | Low | `buffer_audio_chunks` wraps `async for chunk in chunks` in `try/except Exception`; when the iterator itself raises, Python does **not** call `aclose()`, leaving the async generator (and any pending `Queue.get`) dangling. | Add an explicit `await chunks.aclose()` in a `finally` block, or refactor to an `asynccontextmanager` that guarantees cleanup. |
| `src/lyra/adapters/nats/nats_outbound_listener.py` | 101–103 | Low | `stop()` cancels stream drain tasks but does not `await` them before clearing `_stream_tasks`, `_stream_queues`, etc.; tasks may still be in `finally` blocks or holding adapter references. | `await asyncio.gather(*tasks, return_exceptions=True)` after cancellation and before clearing dicts. |
| `src/lyra/adapters/discord/discord_inbound.py` | 255, 261 | Low | Mutable `adapter._owned_threads` and `adapter._thread_sessions` are passed by reference into `InboundContext` and mutated by concurrent pipeline runs without explicit locks. | Document as intentionally shared-by-ref (current safety relies on no-yield atomicity), or guard with `asyncio.Lock` if pipeline pre-hooks ever yield inside mutation blocks. |
| `src/lyra/adapters/discord/discord_voice.py` | 246–251 | Low | `VoiceSessionManager.stream()` uses `async for chunk in chunks` without a `finally`/`aclose()` guard; if `send_streaming` raises, the caller’s iterator may not be closed. | Ensure the caller (or a wrapper) invokes `await chunks.aclose()` on exception; add a note in docstring until S7 absorbs stream lifecycle into `OutboundEmitter`. |

---

## Metrics

| Category | Count | % of async-def sites (≈45) |
|----------|-------|---------------------------|
| Blocking sync I/O in async path | 4 | 9% |
| Resource leaks (fd, session, unawaited tasks) | 3 | 7% |
| Async generator cleanup gaps | 2 | 4% |
| Shared mutable state without explicit sync | 2 | 4% |
| **Total findings** | **8** | **18%** |

---

## Recommendations (prioritized)

1. **Fix Telegram audio fd leak + blocking I/O** (`telegram_audio.py`)
   - Close the `mkstemp` fd immediately after creation (or use `asyncio.to_thread` + `os.close`).
   - Wrap `stat`, `read_bytes`, and `unlink` in `asyncio.to_thread()` so disk I/O never blocks the event loop.

2. **Close aiogram Bot session in `TelegramAdapter.close()`** (`telegram.py`)
   - Add the missing `await self._bot.session.close()` (or aiogram v3 equivalent) to prevent leaking aiohttp connection pools across adapter restarts or test suites.

3. **Await cancelled stream drain tasks in `NatsOutboundListener.stop()`** (`nats_outbound_listener.py`)
   - After `task.cancel()`, gather all tasks with `return_exceptions=True` before clearing internal dicts so `finally` blocks and generator cleanup complete deterministically.

4. **Guarantee async generator cleanup in `buffer_audio_chunks`** (`_shared_audio.py`)
   - Add `try/finally` around the `async for` and call `await chunks.aclose()` in the `finally` block. This closes the iterator even when the stream is interrupted by an exception, preventing stranded `Queue.get()` futures in `decode_stream_events`.

5. **Move blocking filesystem validation out of `TelegramAdapter.__init__`** (`telegram.py`)
   - `Path.is_dir()` and `os.access()` can stall on slow/network mounts. Perform these checks in an async `setup()` hook or wrap them in `asyncio.to_thread()` so constructor never blocks the event loop.
