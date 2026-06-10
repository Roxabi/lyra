# 1807 — omp-rpc Groundwork: Python RPC Client API & Event Contract

**Spike**: Can `oh-my-pi`'s Python `omp-rpc` client drive an agent turn inside a NATS worker, replacing `claude -p`?
**Scope**: READ-ONLY. Every claim carries a `file:line` citation. Un-citeable items → §9.

---

## §1 Scope & Signal

**Signal from memory note** (`project-agent-runtime-eval-goose-vs-pi.md`):
- Pi-family wins over Goose for NATS workers
- `oh-my-pi` / `omp-rpc`: native `steer()` = Shape-D primitive, turnkey LiteLLM provider, `python/omp-rpc` typed client
- Caveats noted: Bun in Quadlet, steer = between-tool-calls, omp-rpc alpha+fork risk

**What this groundwork verifies**: exact API surface of `omp_rpc.RpcClient` and its event model, mapped against the factory clipool worker contract (`clipool_worker.py`).

Source tree:
- `external_repos/oh-my-pi/python/omp-rpc/src/omp_rpc/` — `__init__.py`, `client.py`, `protocol.py`, `host_tools.py`
- `external_repos/oh-my-pi/python/omp-rpc/README.md`
- `src/factory/adapters/clipool/clipool_worker.py` (worktree)

---

## §2 Lifecycle Mapping

Factory control ops (from `clipool_worker.py:_dispatch_control()`):

| Factory op | `cmd` field | Factory call | omp-rpc equivalent | Citation |
|---|---|---|---|---|
| `reset` | — | `pool.reset(pool_id)` | `client.new_session()` | `client.py:630–640` |
| `resume_and_reset` | `cmd.session_id` | `pool.resume_direct(pool_id, session_id)` | `client.switch_session(session_path)` — **gap**: factory passes a session_id (str), omp-rpc takes a `session_path` (file path); mapping unclear | `client.py:651–660` |
| `switch_cwd` | `cmd.cwd` | `pool.switch_cwd(pool_id, resolved)` | **No runtime method.** `cwd=` is constructor-only param (`RpcClient.__init__` kwarg) | `client.py:279` |

### Key lifecycle methods

```
new_session(parent_session: str | None = None) -> CancellationResult   # client.py:630
switch_session(session_path: str | Path) -> CancellationResult          # client.py:651
get_state() -> SessionState                                              # client.py:602
set_model(provider, model_id) -> ModelInfo                              # client.py:670
abort() -> None                                                          # client.py:910
abort_and_prompt(message, *, images=None) -> None                       # client.py:913
```

**`new_session()` semantics** (`client.py:630`): cancels any active turn, starts a fresh session. Equivalent to factory `reset`.

**`switch_session()` semantics** (`client.py:651`): loads a session from a file path on disk — this is a session-file concept, not a raw session-id string. The factory's `resume_and_reset(session_id)` passes a NATS KV session-id string. Direct mapping requires either a session-id→file lookup layer or use of `provider_session_id` kwarg at construction time (`client.py:285`).

**`switch_cwd` gap**: `cwd=` is set at process spawn time only. No `switch_cwd` RPC command exists in the read surface. Factory's per-turn CWD switching requires re-spawning the `RpcClient` or is not directly supported.

### Process lifecycle

```
with RpcClient(...) as client:   # spawns subprocess.Popen, waits on _ready Event
    client.prompt_and_wait(...)
# __exit__ → stop() → terminate + join
```
(`client.py:350–420`, `client.py:440–460`)

Single `RpcClient` = single `omp` subprocess. Long-lived workers can reuse across turns via `new_session()`.

---

## §3 Event Stream → Factory Shapes

Factory event shapes (`clipool_worker.py`):

### `TextLlmEvent`

| Factory field | omp-rpc source | Citation |
|---|---|---|
| `.text` (str) | `MessageUpdateEvent.assistant_message_event["delta"]` when `type == "text_delta"` | `protocol.py:607` (`AssistantTextDeltaEvent.delta`) |

Listener hook: `client.on_message_update(fn)` — fires on every `MessageUpdateEvent`.

Alternative batch path: `prompt_and_wait()` → `PromptTurn.assistant_text` (full concatenated text, not streaming) — `client.py:923–942`, `protocol.py` PromptTurn.

### `ToolUseLlmEvent`

| Factory field | omp-rpc source | Citation |
|---|---|---|
| `.tool_name` | `ToolExecutionStartEvent.tool_name` | `protocol.py:934` |
| `.tool_id` | `ToolExecutionStartEvent.tool_call_id` | `protocol.py:933` |
| `.input` | `ToolExecutionStartEvent.args` (`JsonValue`) | `protocol.py:935` |

Listener hook: `client.on_tool_execution_start(fn)`.

### `ResultLlmEvent`

| Factory field | omp-rpc source | Citation |
|---|---|---|
| `.is_error` (bool) | `AssistantErrorEvent.reason == "error"` (`reason ∈ {"aborted","error"}`) | `protocol.py:668` |
| `.session_id` (str\|None) | `SessionState.session_id` via `client.get_state()` after turn | `protocol.py:758` |
| `.worker_error` | Must be set from `RpcError` / `RpcProcessExitError` catch — no native field | `client.py:147–165` (error hierarchy) |

Turn end: `AgentEndEvent` (`protocol.py:1027`) — `messages: tuple[AgentMessage, ...]` — signals full agent completion. Use `client.on_agent_end(fn)` or `prompt_and_wait()` return.

### Streaming path for factory wire

```python
# Recommended: listener-based (streaming TextLlmEvent)
def on_msg(event: MessageUpdateEvent) -> None:
    ae = event.assistant_message_event
    if ae.get("type") == "text_delta":
        emit TextLlmEvent(text=ae["delta"])

def on_tool_start(event: ToolExecutionStartEvent) -> None:
    emit ToolUseLlmEvent(tool_name=event.tool_name,
                         tool_id=event.tool_call_id,
                         input=event.args)

client.on_message_update(on_msg)
client.on_tool_execution_start(on_tool_start)
turn = client.prompt_and_wait(text)   # blocks until AgentEndEvent
emit ResultLlmEvent(is_error=False,
                    session_id=client.get_state().session_id)
```

---

## §4 Session ID Surface

`SessionState.session_id: str` — available via `client.get_state()` after `start()` or after any turn. (`protocol.py:758`)

`SessionState.session_file: str | None` — path to session file on disk; used by `switch_session()`. (`protocol.py:762`)

`provider_session_id: str | None` — passed at construction as `--provider-session-id` CLI arg; lets host inject a cloud-side session id. (`client.py:285`, `_build_command()` at `client.py:1483`)

**Gap for `resume_and_reset`**: factory stores session_id strings in NATS KV (`clipool_worker.py`). `switch_session()` takes a file path, not an id. Bridging requires: (a) maintaining a `session_id → session_file` map, or (b) using `provider_session_id` + `new_session(parent_session=session_id)` where `parent_session` is passed to the new session seed — the exact semantics of `parent_session` are unverified (§9).

---

## §5 Host-Tool Bridge

### HostTool contract

```python
@dataclass(slots=True, frozen=True)
class HostTool(Generic[TParams, TDetails]):
    name: str
    description: str
    parameters: JsonObject          # JSON Schema
    execute: Callable[[TParams, HostToolContext[TDetails]], HostToolResultValue]
    label: str | None = None
    hidden: bool = False
    decode: Callable[[JsonObject], TParams] | None = None
```
(`host_tools.py:30–50`)

### CRITICAL: `execute` is SYNCHRONOUS

`tool.execute(params, context)` is called from a `threading.Thread` spawned in `_handle_host_tool_call()`. (`client.py:1194–1237`)

The thread runs `run_tool()` which calls `tool.execute(params, context)` synchronously. (`client.py:1208`)

**Factory workers are asyncio-based.** Any host-tool that needs to call async factory internals (NATS publish, coroutine-based I/O) must bridge via `asyncio.run_in_executor()` or `loop.call_soon_threadsafe()` + `asyncio.Future`. This is a mandatory bridging pattern — not a native fit.

### HostToolContext

```python
@dataclass(slots=True)
class HostToolContext(Generic[TDetails]):
    tool_call_id: str
    _cancel_event: threading.Event
    _send_update: Callable[[JsonValue], None]

    @property
    def cancelled(self) -> bool: ...
    def send_update(self, result: HostToolResultValue) -> None: ...
```
(`host_tools.py:55–80`)

`send_update()` fires `host_tool_update` notification back over stdio — used for streaming partial results. Synchronous, safe to call from the tool thread.

### Registration

`client.set_custom_tools(tools)` — sends `set_host_tools` RPC command, returns registered tool names. (`client.py:826–850`)
Also accepted at construction: `custom_tools=` kwarg to `RpcClient.__init__`. (`client.py:288`)

---

## §6 Steer (Between-Tool-Call Control)

```python
def steer(self, message: str, *, images: Sequence[ImageContent] | None = None) -> None:
    self._request("steer", message=message, images=list(images) if images is not None else None)
```
(`client.py:892–899`)

`steer()` sends a `steer` RPC command. It does NOT acquire `_prompt_lifecycle` — unlike `prompt_and_wait()` / `collect_events()` / `wait_for_idle()` which all go through `_prompt_lifecycle.acquire()`. (`client.py:892` vs `client.py:923–942`)

This confirms `steer()` is designed to be called **while a prompt turn is in flight** — it injects a steering message between tool calls.

`follow_up()` is a separate method for post-turn follow-up messages. (`client.py:901–908`)

**`SteeringMode`**: `Literal["all", "one-at-a-time"]` — controllable via `client.set_steering_mode(mode)`. (`protocol.py` `SteeringMode` TypeAlias, `client.py` `set_steering_mode`)

`steer()` semantics verified from source: it is a fire-and-forget RPC command that does not block the lifecycle coordinator. Between-tool-call injection is the stated use case (README `steer` mention + memory note). No explicit "between tool calls only" guard seen in Python layer — wire-level enforcement is in the `omp` process itself. Consider this partially confirmed.

---

## §7 Provider Re-Pointing

### At construction (restart required)

```python
RpcClient(provider="anthropic", model="claude-sonnet-4-5")
RpcClient(model="openrouter/anthropic/claude-sonnet-4.6")  # OpenRouter prefix
```
(`README.md:22`, `README.md:38`)

CLI args built by `_build_command()`: `--provider <p> --model <m>`. (`client.py:1471–1475`)

`env=` kwarg accepts a mapping: `Mapping[str, str]`. Passed to `subprocess.Popen`. Can inject `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, etc. (`client.py:280`, `client.py:350–420` `start()`)

### At runtime (no restart)

```python
model_info = client.set_model(provider, model_id) -> ModelInfo
```
(`client.py:670`)

Sends `set_model` RPC command. Returns `ModelInfo` with `id`, `name`, `api`, `provider`, `base_url`, `cost`, etc. (`protocol.py:parse_model_info:1131`)

**LiteLLM integration**: omp-rpc's provider system routes through LiteLLM internally (per memory note). The `openrouter/...` model prefix convention is LiteLLM-style. No direct code evidence in read surface — treat as unverified (§9).

`provider_session_id` kwarg: cloud-side session continuation across `RpcClient` restarts. (`client.py:285`, `_build_command():1483`)

---

## §8 Risk Matrix

| Risk | Severity | Confidence | Mitigation |
|---|---|---|---|
| **`HostTool.execute` is SYNC** — factory workers are async | HIGH | CONFIRMED (`host_tools.py:42`, `client.py:1208`) | Bridge via `loop.run_in_executor()` or dedicated sync→async adapter; adds complexity |
| **`switch_cwd` not supported at runtime** — `cwd=` constructor-only | HIGH | CONFIRMED (`client.py:279`, no `switch_cwd` RPC found in 1796L client.py) | Requires process re-spawn per CWD change, or re-architecture: move CWD to prompt text / agent config |
| **`resume_and_reset` session_id→file gap** — factory stores KV string ids, omp-rpc `switch_session()` takes a file path | MEDIUM | CONFIRMED (`client.py:651`, `protocol.py:762`) | Add `session_id → session_file` registry in worker state; or use `provider_session_id` + `new_session()` |
| **Single-flight constraint** — one `prompt_and_wait()` active per `RpcClient` | MEDIUM | CONFIRMED (`client.py:932`, README:242) | Already matches factory's per-slot model; pool = N `RpcClient` instances |
| **`omp` subprocess in Quadlet** — `omp` is a Bun/Node binary; Quadlet container must include Node/Bun runtime | MEDIUM | UNVERIFIED (memory note mentions "Bun in Quadlet" caveat; no Dockerfile/image read) | Check `oh-my-pi` distribution format; may need custom container layer |
| **`steer()` wire semantics** — Python sends command but enforcement is in `omp` process | LOW | PARTIALLY CONFIRMED (client.py:892 confirms no lifecycle lock; README confirms intent) | Trust omp wire protocol; test with `steering_mode="one-at-a-time"` |
| **Alpha/fork risk** — `omp-rpc` is in `external_repos/` (not published to PyPI) | LOW-MEDIUM | CONFIRMED (path: `external_repos/oh-my-pi`) | Pin to a commit; vendor or fork if needed |
| **Thread-safety of listener callbacks** — fired from stdout reader daemon thread | LOW | CONFIRMED (`client.py:1566`) | Listeners must be thread-safe; use `loop.call_soon_threadsafe()` to dispatch to asyncio |

---

## §9 Not Audited / Unverified Claims

Items that could not be source-confirmed within the reading budget:

1. **`parent_session` semantics in `new_session(parent_session=session_id)`** — method signature confirmed (`client.py:630`) but the wire behavior of `parent_session` (whether it seeds history from a prior session file or just names the new session) was not traced into the `omp` process. Treat as unverified.

2. **LiteLLM internal routing** — memory note says omp routes through LiteLLM. Not confirmed from read Python source. The `openrouter/` prefix convention is consistent with LiteLLM but not explicitly documented in `omp_rpc/`.

3. **`omp` binary distribution format** — whether `omp` ships as a standalone binary, a Bun bundle, or requires a Node/Bun runtime in the container image. `_build_command()` uses `executable: str = "omp"` (`client.py:275`). No Dockerfile or packaging metadata read.

4. **`AgentStartEvent` not re-exported from `__init__.py`** — appears in `client.py` imports and `protocol.py` definition but absent from `__init__.py` exports (`__init__.py:1–189`). May be intentionally internal; host code using it would need to import from `omp_rpc.protocol` directly.

5. **`set_steering_mode("one-at-a-time")` behavior with `steer()`** — steering mode affects when steer messages are applied; exact interaction with `steer()` mid-turn not traced to wire docs. `rpc.md` not read.

6. **`switch_session()` vs `new_session(parent_session=...)` for `resume_and_reset`** — factory needs to resume a prior session by id. Which method achieves this (file-path resume vs parent-seeded new session) is unclear without tracing into `omp` wire protocol. `rpc.md` not read.

7. **`ToolExecutionStartEvent` → `ToolUseLlmEvent.input` type match** — `ToolExecutionStartEvent.args: JsonValue` (`protocol.py:935`) vs factory `ToolUseLlmEvent.input` type (confirmed as present in `clipool_worker.py` but exact type annotation not re-verified in this session).

---

## Summary Table

| Axis | Status | Key evidence |
|---|---|---|
| Lifecycle complete (reset/resume/cwd) | PARTIAL — reset OK, resume has id→file gap, switch_cwd unsupported at runtime | `client.py:630`, `client.py:651`, `client.py:279` |
| Events map clean (text/tool/result) | YES — clean 1:1 mapping via listener hooks | `protocol.py:607,933–935,668,758` |
| Host-tool bridge native | NO — execute is SYNC, async bridge mandatory | `host_tools.py:42`, `client.py:1208` |
| Steer between-tool-call confirmed | MOSTLY — Python-layer confirmed, wire enforcement unverified | `client.py:892` |
| Provider re-pointable | YES — runtime `set_model()` + OpenRouter prefix confirmed | `client.py:670`, `README.md:38` |
