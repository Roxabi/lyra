### Summary
- 2 high-severity leak paths: partial adapter wiring failure in standalone adapters leaves stores/NATS subscriptions uncleaned; unified mode skips `cli_pool.stop()` and orphan clipool worker task when `run_lifecycle` raises.
- 3 medium-severity blocking-in-async issues: synchronous SQLite migration, synchronous `subprocess.run` in clipool bootstrap, and sync file I/O in credentials/lockfile helpers.
- Zero race conditions in shared mutable state: all bootstrap state is either local to the coroutine or accessed from a single asyncio thread.

### Findings
| File | Line | Severity | Description | Recommendation |
|---|---|---|---|---|
| `src/lyra/bootstrap/standalone/adapter_standalone.py` | 81-135, 215-280 | **High** | If `adapter.astart()` raises inside the per-bot wiring loop, previously-wired adapters, NATS buses, typing listeners, and the opened `TurnStore`/`ThreadStore` are never cleaned up because no `try/finally` wraps the loop. | Wrap each bot iteration in `contextlib.AsyncExitStack` or add a `finally` block that walks `wired`/`wired_dc` and closes every accumulated resource. |
| `src/lyra/bootstrap/factory/unified.py` | 61-74, 76-91 | **High** | `clipool_worker_task` is created before `run_lifecycle`; if `run_lifecycle` raises, the task is never cancelled. Additionally, `cli_pool.stop()` is missing from the `finally` block — only `drain_audit_tasks()` and `cli_nats_driver.stop()` are called. | Add a dedicated `try/finally` around `clipool_worker_task` lifetime: `task.cancel(); await asyncio.gather(task, return_exceptions=True)` in `finally`. Add `await clipool.cli_pool.stop()` to the existing `finally` block. |
| `src/lyra/bootstrap/bootstrap_stores.py` | 66-162, 239-298 | **Medium** | `_atomic_table_copy` performs synchronous SQLite I/O (`sqlite3.connect`, `shutil.move`, `tempfile.mkstemp`) inside the async `open_stores` context manager, blocking the event loop during migration. | Offload the migration block to `asyncio.to_thread()` or replace with `aiosqlite` for consistency with the rest of the async codebase. |
| `src/lyra/bootstrap/standalone/hub_standalone.py` | 68, 301 | **Medium** | `acquire_lockfile()` is not paired with `release_lockfile()` in a `try/finally`. If the async body raises, the lockfile persists until process exit, potentially blocking restarts. | Wrap the entire function body in `try: ... finally: release_lockfile()`. |
| `src/lyra/bootstrap/infra/git_ownership_probe.py` | 38 | **Medium** | `subprocess.run(..., check=False)` is a blocking synchronous call inside async `_bootstrap_clipool_standalone()`. | Replace with `asyncio.create_subprocess_exec` and `await proc.wait()`. |
| `src/lyra/bootstrap/standalone/hub_standalone.py` | 82-299 | **Low** | `nc.close()` sits outside the `async with open_stores(...)` body. If store teardown inside `__aexit__` raises, `nc.close()` is skipped. | Move `nc.close()` into an outer `try/finally` that wraps the entire `async with` block. |
| `src/lyra/bootstrap/credentials.py` | 50, 68 | **Low** | `Path.read_text()` is synchronous file I/O called from async adapter wiring. Files are tiny (Podman secrets) so impact is minimal but non-zero. | Wrap in `asyncio.to_thread()` for consistency, or document as a cold-path startup-only call. |
| `src/lyra/bootstrap/infra/lockfile.py` | 42, 74 | **Low** | `lf.read_text()` / `lf.write_text()` are sync file I/O. In practice called before the async loop starts, but the helper is sync and may be invoked from async contexts. | Document as pre-async-only, or wrap in `asyncio.to_thread()`. |
| `src/lyra/bootstrap/standalone/hub_standalone.py` | 180-181 | **Low** | `audit_sink = JetStreamAuditSink(); await audit_sink.provision(nc)` provisions an async resource that is never referenced again and never explicitly stopped. | Either wire `audit_sink` into a downstream consumer and stop it during teardown, or remove if dead code (noting prior audit covered dead-code). |

### Metrics
| Metric | Count |
|---|---|
| Files analyzed | 31 |
| Blocking-in-async findings | 3 (SQLite migration, subprocess, file I/O) |
| Resource leak paths | 3 (adapter wiring, clipool/lifecycle, lockfile) |
| Fire-and-forget / orphan task | 1 (clipool_worker_task on `run_lifecycle` exception) |
| Race conditions in shared state | 0 |
| `async for` / generator cleanup issues | 0 |
| Total findings | 9 |
| High severity | 2 |
| Medium severity | 3 |
| Low severity | 4 |

### Recommendations (prioritized)
1. **Fix adapter_standalone wiring cleanup (High)** — Add per-iteration or per-branch `finally` blocks in both Telegram and Discord wiring loops. Use `AsyncExitStack` to accumulate `(adapter, inbound_bus, typing_listener)` entries and ensure `close()`/`stop()` is called on every item even when an exception aborts the loop.
2. **Fix unified clipool lifecycle (High)** — In `_bootstrap_unified`, wrap `clipool_worker_task` in a `try/finally` that always cancels and awaits it. Add `await clipool.cli_pool.stop()` to the existing `finally` block so the reaper and subprocesses are drained on both success and exception paths.
3. **Offload synchronous DB migration to thread (Medium)** — Wrap `_atomic_table_copy` invocation inside `open_stores` with `asyncio.to_thread()` so the SQLite blocking work does not stall the event loop during container cold-start.
4. **Pair lockfile acquire/release in hub_standalone (Medium)** — Introduce an outer `try/finally` in `_bootstrap_hub_standalone` so `release_lockfile()` is guaranteed to run. Verify `unified.py` already has this pattern and keep them consistent.
5. **Replace sync subprocess in git probe (Medium)** — Convert `run_git_ownership_probe` to async using `asyncio.create_subprocess_exec` to avoid blocking the event loop during clipool startup.
