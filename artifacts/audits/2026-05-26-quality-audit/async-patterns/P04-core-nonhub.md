## Async Patterns Audit — P04: src/lyra/core/**/*.py (excl. hub/)

**Date:** 2026-05-26
**Scope:** 88 Python files
**Prior audit (2026-05-18):** Hexagonal conformance, duplication, dead-code. Do not re-report unless regressed.

---

### Summary

- **2 high-severity blocking issues** in `agent_refiner.py`: sync `subprocess.run` inside an async-adjacent path and a nested `asyncio.run()` that will crash when called from an existing event loop.
- **3 medium-severity blocking-I/O leaks** in `json_agent_store.py` and `runtime_config.py` where sync file operations run directly on the event loop.
- **2 medium-severity fire-and-forget task leaks** in `session_lifecycle.py` and `pool_processor_streaming.py` where exceptions are swallowed and a redundant task wrapper wastes resources.
- Cancel-in-flight, feeder, reaper, and streaming lifecycles are correctly supervised with proper `cancel`/`await`/`gather` patterns. No regressions from the 2026-05-18 audit.

---

### Findings

| File | Line | Severity | Description | Recommendation |
|---|---|---|---|---|
| `agent_refiner.py` | 137 | **High** | `CliLlmProvider.chat()` uses sync `subprocess.run(...)` to shell out to `claude --print`. When called from any asyncio context (e.g. an agent command handler), this blocks the event loop for the full LLM turn. | Replace with `asyncio.create_subprocess_exec` or run in `asyncio.to_thread`. |
| `agent_refiner.py` | 276 | **High** | `AgentRefiner.apply_patch()` calls `asyncio.run(_apply())`. This nests an event loop and will raise `RuntimeError` if called from an existing asyncio loop (the normal Lyra runtime). | Remove `asyncio.run`; make `apply_patch` async and let callers await it directly. |
| `json_agent_store.py` | 71 | Medium | `connect()` (async) reads the entire JSON backing file with sync `Path.read_text()` on the event loop. | Wrap in `asyncio.to_thread` or switch to `aiofiles`. |
| `json_agent_store.py` | 227 | Medium | `_persist()` (sync) writes the entire JSON backing file with `Path.write_text()` and is called from every async write path (`upsert`, `delete`, `set_bot_agent`, etc.). | Wrap in `asyncio.to_thread` or switch to `aiofiles`. |
| `runtime_config.py` | 119-120 | Medium | `RuntimeConfig.load()` (async) opens the TOML file with sync `path.open("rb")` + `tomllib.load()` on the event loop. | Wrap in `asyncio.to_thread`. |
| `session_lifecycle.py` | 80 | Medium | `_schedule_extraction()` fires `asyncio.create_task(coro)` for background concept/preference extraction. If `task_registry` is `None`, the task is pure fire-and-forget with **no exception handling** — failures are silently swallowed. Even when a registry exists, there is no `add_done_callback` that logs exceptions. | Always attach a done callback that logs exceptions, or use a supervised `TaskGroup` / `asyncio.gather` at shutdown. |
| `pool_processor_streaming.py` | 112 | Medium | `await asyncio.create_task(processor.post(...))` wraps a coroutine in a task only to await it immediately. This is redundant and creates an extra Task object per streaming turn with no benefit. | Replace with direct `await processor.post(...)`. |
| `pool_processor_exec.py` | 209-214 | Low | `dispatch_streaming()` is awaited inside a `try/except BaseException`, but the `result` async generator (from `build_streaming_capture`) is **not explicitly closed on the exception path**. The generator's `finally` block (which calls `aclose` on the underlying CLI subprocess iterator) may not run until GC. | Add a `finally` block that calls `await result.aclose()` before re-raising. |
| `command_loader.py` | 112, 148, 200 | Low | `discover()`, `load()`, and `reload()` perform sync `open()` + `tomllib.load()` and `importlib.util.spec_from_file_location` + `exec_module`. These are sync methods called from async contexts during runtime plugin loading. | Acceptable for infrequent admin commands; for hot-reload paths, wrap in `asyncio.to_thread`. |
| `messages.py` | 44-45 | Low | `MessageManager.__init__` does sync `open(path, "rb")` + `tomllib.load()`. Called once at bootstrap; not a runtime concern. | No action required — startup-only. |

---

### Metrics

| Metric | Count |
|---|---|
| Files scanned | 88 |
| Files containing `async def` | ~35 |
| `asyncio.create_task` calls | 8 |
| `asyncio.gather` calls | 2 |
| `asyncio.wait_for` calls | 11 |
| `asyncio.sleep` calls | 3 |
| `async for` loops | 4 |
| Blocking sync calls in async contexts | 5 |
| Nested event loops | 1 |
| Unsupervised fire-and-forget tasks | 2 |
| Properly cancelled/awaited background tasks | 5/7 (71%) |

---

### Recommendations (prioritized)

1. **Fix nested event loop in `AgentRefiner.apply_patch`** (#2, High). This is a guaranteed crash in the normal async runtime. Make the method async and propagate the change up to callers.
2. **Replace `subprocess.run` in `CliLlmProvider.chat`** (#1, High). Use `asyncio.create_subprocess_exec` or `asyncio.to_thread` to avoid blocking the event loop during LLM refinement.
3. **Harden background extraction tasks in `SessionManager`** (#6, Medium). Attach an `add_done_callback` that logs exceptions and removes the task from the registry. If `task_registry` is `None`, either require a registry or use a module-level weak set.
4. **Offload sync file I/O in `JsonAgentStore` and `RuntimeConfig`** (#3-#5, Medium). Wrap `Path.read_text/write_text` and `tomllib.load` in `asyncio.to_thread`. These are small files, but in a high-concurrency hub they can stall the loop.
5. **Add explicit `aclose()` guard in `process_one`** (#8, Low). Ensure the streaming capture generator is deterministically closed on the exception path so CLI subprocess pipes are released promptly.
