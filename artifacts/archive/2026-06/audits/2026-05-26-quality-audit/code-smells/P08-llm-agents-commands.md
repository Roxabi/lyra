### Summary
- `SimpleAgent` is the single hotspot: 109-line `process()` with `noqa: C901`, god-class shape (10 methods, 6+ responsibilities), and two feature-envy private-field accesses.
- `agent_cmd` commands repeat the same `async def _run() → _connect_store() → try/finally close` scaffolding 12 times across 3 files — largest DRY surface in the partition.
- Stale exemption: `src/lyra/llm/drivers/cli_nats.py` is listed in `file_exemptions.txt` but no longer exists.

### Findings
| File | Line | Severity | Description | Recommendation |
|---|---|---|---|---|
| `src/lyra/agents/simple_agent.py` | 205 | High | `process()` spans 109 lines, `noqa: C901`; mixes voice pre-router, STT error handling, session linking, streaming vs complete path, and backend-error taxonomy. | Extract `_rewrite_voice_message()`, `_link_session()`, and `_build_error_response()` helpers. |
| `src/lyra/agents/simple_agent.py` | 48 | High | `SimpleAgent` god class: 10 methods spanning LLM routing, STT, voice rewriting, session callback wiring, runtime config reload, and command registration. | Decompose into `VoicePreprocessor`, `SessionLinker`, or move wiring to bootstrap composition root. |
| `src/lyra/llm/llm_client.py` | 47 | Medium | `LlmClient` god class: 11 methods covering codec wrapping, control-plane ops, session store, and liveness. | Extract control-plane surface into `LlmControlClient` or fold into driver abstraction. |
| `src/lyra/llm/llm_client.py` | 156 | Medium | Feature envy: 3× access to `self._pool._transport.call()` with `SLF001` noqa. | Promote `call()` to public protocol on `WorkerPoolClient`, or add `send_control()` wrapper. |
| `src/lyra/agents/simple_agent.py` | 242 | Medium | Feature envy: reads `pool._system_prompt` and writes `pool._last_turn_had_backend_error` (private attributes). | Add public accessors on `Pool`, or move error tracking to `TurnContext`. |
| `src/lyra/llm/decorators.py` | 17 / 102 | Medium | DRY: `RetryDecorator` and `CircuitBreakerDecorator` repeat identical `complete`/`stream`/`is_alive` signatures and `self.capabilities = inner.capabilities` (~30 lines each). | Introduce `LlmProviderDecorator` base with `__getattr__` forwarding or dataclass-driven delegation. |
| `src/lyra/agent_cmd/agents/edit_cmd.py` | 78 | Low | DRY: 5 commands repeat `async def _run() → store = await _connect_store(); try: ... finally: await store.close()` (~7 lines × 5). | Extract `@with_store` decorator or `run_with_store(async_fn)` in `lyra.cli_agent`. |
| `src/lyra/agent_cmd/agents/edit_cmd.py` + `init.py` + `list_cmd.py` | 34 / 21 | Low | DRY: same `_run` + `asyncio.run(_run())` + `store.close` pattern appears 12 times across 3 files. | Same shared helper as #7. |
| `src/lyra/agents/simple_agent.py` | 160 | Low | DRY: `_maybe_register_reset` and `_maybe_register_resume` are structurally identical (`if _cli_pool / elif _cli_nats_driver` with local captures). | Extract `_register_callback(pool, fn_name, *args)` helper. |
| `src/lyra/commands/identity/handlers.py` | 30 | Low | Feature envy: `_any_alias_blocked` reaches into `hub._authenticators` and `auth._store.check()`. | Add `is_blocked(alias)` to hub public API or alias store. |
| `tools/file_exemptions.txt` | — | Low | Stale exemption: `src/lyra/llm/drivers/cli_nats.py` is listed but file no longer exists. | Remove entry from `file_exemptions.txt`. |
| `src/lyra/agent_cmd/agents/edit_cmd.py` | — | Low | God module: 288 lines covering edit, assign, unassign, patch, refine, plus TTS helpers (~6 responsibilities). | Split into per-command modules (`edit.py`, `assign.py`, `patch.py`, `refine.py`). |
| `src/lyra/llm/drivers/cli.py` | — | Low | `ClaudeCliDriver` has 8 methods; 5 are thin passthroughs to `CliPool`. | Collapse to a single adapter method, or use `CliPool` directly via protocol if possible. |

### Metrics
- **Files analyzed**: 29 (2,246 total lines)
- **Files >100 lines**: 8 / 29 (27.6%)
- **Functions >100 lines**: 1 (`SimpleAgent.process`, 109 lines)
- **God classes (>5 responsibilities)**: 3 (`SimpleAgent`, `LlmClient`, `ClaudeCliDriver`)
- **DRY violation clusters**: 3 (decorator signatures, agent_cmd `_run` scaffolding, callback wiring)
- **Feature envy instances**: 3 (`LlmClient` → `_transport`, `SimpleAgent` → `Pool._*`, identity → `hub._authenticators`)
- **Cognitive complexity >15**: 1 (`SimpleAgent.process`, confirmed by `noqa: C901`)
- **Stale exemptions**: 1 (`cli_nats.py`)

### Recommendations (prioritized)
1. **Extract `SimpleAgent.process()` into stage helpers** — split voice pre-router, session linking, and error-response builder into private methods. This single change drops the function below 50 lines and removes the `C901` waiver.
2. **Add a `@with_store` decorator in `lyra.cli_agent`** — wrap the `async def _run() → _connect_store() → try/finally close → asyncio.run` pattern once, delete ~80 lines of boilerplate across `edit_cmd.py`, `init.py`, and `list_cmd.py`.
3. **Introduce `LlmProviderDecorator` base class** — delete the duplicated method signatures and `capabilities` forwarding in `RetryDecorator` and `CircuitBreakerDecorator` (~25 lines each).
4. **Promote `WorkerPoolClient.call()` to public protocol** — remove the 3× `SLF001` feature-envy violations in `LlmClient` by adding a first-class `send_control(subject, payload)` method on the pool client.
5. **Split `edit_cmd.py` into per-command modules** — create `edit.py`, `assign.py`, `patch.py`, `refine.py` under `agent_cmd/agents/` to bring the folder under the 12-file folder-size gate and align with the per-command registration pattern already used in `commands/`.
