### Summary
- 1 critical blocking-event-loop bug in `lyra agent refine` (sync `input()` + `subprocess.run()` called inside async CLI coroutine).
- 4 async-callback fragilities (sync lambdas returning coroutines, private transport access, missing timeout defaults, blocking unlink in finally).
- No unclosed NATS subscriptions, no unawaited fire-and-forget tasks, no shared-state race conditions in the surveyed files.

### Findings

| File | Line | Severity | Description | Recommendation |
|------|------|----------|-------------|----------------|
| `src/lyra/agent_cmd/agents/edit_cmd.py` | 264 | **Critical** | `refiner.run_session(io)` is a **synchronous** method that blocks the asyncio event loop with `input()` prompts and `subprocess.run(["claude", "--print", ...])`. Called inside `_run()` which is executed via `asyncio.run()`. | Convert `run_session` to `async def` and replace `input()` with `await asyncio.to_thread(input, ...)`; replace `subprocess.run()` with `asyncio.create_subprocess_exec` or offload to thread. |
| `src/lyra/agents/simple_agent.py` | 164, 171, 186, 192 | **High** | Sync lambdas return coroutine objects (`lambda: _cli_pool.reset(_pool_id)`) for `Pool.register_session_callbacks`, which expects `Callable[[], Awaitable[None]]`. While the caller `await`s the result, if the callback is ever invoked without `await` (e.g. in a sync context or stored), the coroutine is silently dropped. | Replace with `async def` helper functions or use `functools.partial` with an `async def` wrapper to make the awaitable nature explicit. |
| `src/lyra/llm/llm_client.py` | 156, 186, 209 | **Medium** | Direct access to `self._pool._transport.call(...)` bypasses the public `WorkerPoolClient` API. If the transport is replaced (reconnect, stop/start), these call-sites break or leak stale connections. | Add control-plane methods to `WorkerPoolClient` (or a dedicated control client) so `LlmClient` does not reach through private attributes. |
| `src/lyra/llm/llm_client.py` | 73, 99 | **Medium** | `complete()` and `stream()` pass `timeout=self._timeout` where `self._timeout` defaults to `None` in `__init__`. Requests with no timeout hang indefinitely if the clipool worker is unresponsive or NATS subject has no responder. | Set a sensible default timeout (e.g. 120s) in `LlmClient.__init__`, or require callers to provide one. |
| `src/lyra/agents/simple_agent_prompts.py` | 93 | **Medium** | `tmp_path.unlink(missing_ok=True)` in a `finally` block runs directly on the event loop thread, blocking it for filesystem I/O. | Offload to thread: `await asyncio.to_thread(tmp_path.unlink, missing_ok=True)`. |
| `src/lyra/llm/llm_client.py` | 69 | **Low** | `stop()` stops the underlying pool heartbeat but does not clear `self._lyra_sessions`. Stale mappings survive client restart. | Add `self._lyra_sessions.clear()` in `stop()`. |
| `src/lyra/llm/decorators.py` | 17, 102 | **Low** | `RetryDecorator` and `CircuitBreakerDecorator` wrap `LlmProvider` but do not expose `stop()` (present on `LlmClient`). If a decorated `LlmClient` is used, calling `.stop()` on the decorator chain fails. | Either add `stop()` to the `LlmProvider` protocol, or make decorators forward unknown attributes to the inner instance. |

### Metrics
- Files surveyed: 25
- Async-def functions/methods: 21
- Blocking calls in event loop: 1 critical + 1 minor
- Unconventional sync-lambda-returning-coroutine: 4 instances
- Private-attribute access for I/O: 3 instances
- Missing/default-null timeouts on network calls: 2 methods
- Async generator cleanup (`aclose` on input iterator): 1 instance (correct, in `StreamProcessor.process`)
- `asyncio.run()` in CLI commands: 6 (expected pattern, no issue)

### Recommendations
1. **Fix blocking `refine` command** (critical): Convert `AgentRefiner.run_session` and `TerminalIO` to async-aware interfaces so the CLI event loop is not frozen during interactive refinement.
2. **Stabilize callback lambdas** (high): Introduce small `async def` wrappers for `reset_fn`, `workspace_fn`, and `resume_fn` in `SimpleAgent._maybe_register_reset` / `_maybe_register_resume`.
3. **Remove private `_transport` access** (medium): Surface `call(subject, payload, timeout)` through `WorkerPoolClient` public API, or add a `control(cmd)` method so `LlmClient` stays within abstraction boundaries.
4. **Add default timeout to `LlmClient`** (medium): Default `self._timeout` to a finite value so unresponsive workers cannot hang the agent indefinitely.
5. **Audit `finally`-block I/O** (low): Apply `asyncio.to_thread` to `tmp_path.unlink` and scan other `finally` blocks in the codebase for similar blocking cleanup.
