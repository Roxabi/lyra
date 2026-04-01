# #477 — ToolHandler Registry + First Tools: Analysis

**Issue:** #477  
**Phase:** 1  
**Scope:** ~300 LOC, zero new external dependencies  
**Date:** 2026-04-01  
**Status:** Pre-implementation analysis

---

## 1. Context

Issue #477 introduces the foundational tool execution layer for Lyra: a `ToolHandler` Protocol, a `ToolRegistry`, and the first three concrete tools (`shell_sandboxed`, `file_read`, `web_fetch`). This is the minimum viable autonomy step — without it, Lyra can describe what to do but cannot act.

Four frameworks were studied for design patterns: Hermes (Python, Nous Research), OpenClaw (TypeScript, pi-agent-core), Pi-mono (TypeScript, Mario Zechner), and OpenFang (Rust, Terracotta). Each contributed specific decisions to Lyra's approach.

---

## 2. Why Tools

### The Pain Today

Lyra is chat-only. Every workflow that requires action breaks the loop:

- "Run this query on the server" → copy-paste by hand
- "Read my project files" → copy-paste by hand
- "Summarize this URL" → copy-paste by hand

The user is the tool executor. Each context switch is friction that accumulates. More critically, several planned subsystems are blocked on tools existing:

```
#477 ToolRegistry → #478 Plugin SDK → Phase 3c
#477 ToolRegistry → #480 CRON scheduler → Phase 4
#477 ToolRegistry → #481 Inter-agent → Phase 4
```

### Gap Scores (from landscape analysis)

| Subsystem | Gap Score | Phase |
|-----------|-----------|-------|
| Tools/MCP | 8/10 (critical) | 1 |
| Memory | 3/10 | 1 |
| LLM fallback | 5/10 | 1–2 |
| Plugins | 7/10 | 3 |

Tools is the highest-priority gap in Phase 1.

### Persona: Yuki

Yuki's goal: "I want to tell Lyra to scan my repo and file a bug. Not describe how, but actually do it."

The trigger is the execution gap. Lyra understands the task; it cannot act on it. Tools are the minimum viable autonomy. Without them, Lyra is expensive autocomplete.

### DX Goals

- Adding a tool = 1 class, 1 registration call, 0 wiring
- New tools auto-appear in LLM tool list via `get_definitions()`
- Sandboxed by default — no footguns for weekend projects
- Tool errors are informative strings, not stack traces

---

## 3. Framework Analysis

### 3.1 Hermes (Nous Research, Python, MIT)

**Architecture:** 3-file split — `tools/registry.py` (singleton), `tools/*.py` (self-registering at import), `model_tools.py` (orchestrator). Toolsets provide composable groups per platform.

**Self-registration pattern:**
```python
# tools/file_read.py
from tools.registry import registry

@registry.register
class FileReadTool:
    name = "read_file"
    description = "Read a file from the filesystem"
    input_schema = { ... }
    def execute(self, params: dict) -> str: ...
```

**Key insights:**
- Self-registration is elegant but adds magic (import order matters). Skip for Phase 1.
- The 3-layer split (registry → orchestrator → toolsets) is the right shape for Lyra.
- `approval.py` is a separate module — dangerous command detection does not belong inside `ShellTool.execute()`.
- Parallel safe tools via `ThreadPoolExecutor(8)` — gate on a `read_only` flag, defer to Phase 2.

**What to take:** The layered architecture and the approval-as-separate-concern pattern.  
**What to skip:** Self-registration via import side effects (explicit is better until there's a plugin SDK).

---

### 3.2 OpenClaw (TypeScript, MIT, pi-agent-core)

**Tool interface:**
```typescript
interface AgentTool {
  name: string
  description: string
  parameters: TSchema  // TypeBox schema
  execute: (toolCallId: string, params: unknown) => Promise<string>
}
```

**Typed error model:**
```typescript
class ToolInputError extends Error {}         // 400 — bad params, LLM should retry with fix
class ToolAuthorizationError extends Error {} // 403 — policy denied, LLM should not retry
```

**Policy pipeline:** Allowlists/denylists, mutation detection, filesystem scope, sandbox constraints — all checked before `execute()`.

**Profiles:** `minimal`, `coding`, `messaging`, `full` — filter which tools the LLM sees. Profile ≠ permission; it controls visibility in the system prompt.

**Lane system:** All turns (including tool calls) go through a lane-based command queue (`main`/`cron`/`subagent`/`session:<key>`). Prevents concurrent writes, serializes per-session, parallelizes across sessions.

**What to take:** TypeBox → raw JSON Schema dict in Python (Anthropic SDK takes raw dict, no library needed). Typed error distinction → encode in return string for Phase 1, typed exceptions for Phase 2. Policy pipeline as thin wrapper around `execute()`.  
**What to skip:** Profiles → Phase 2 pattern. Lane system → Phase 2 (Lyra is single-session Phase 1).

---

### 3.3 Pi-mono (Mario Zechner, TypeScript, MIT)

**Architecture:**
```
packages/ai           — unified multi-provider LLM API
packages/agent        — pi-agent-core: tool calling + state management
packages/coding-agent — user-facing CLI (4 default tools)
packages/mom          — Slack bot built on agent-core (SDK pattern)
```

**The 4 default tools:** `read`, `write`, `edit`, `bash` — nothing else. Proves the minimum viable tool set is 4.

**3 distinct extension mechanisms:**
- **Tools** — executable handlers the LLM calls
- **Skills** — markdown files injected into system prompt (`~/.pi/agent/skills/`)
- **Extensions** — TypeScript lifecycle hooks (session start/end, tool before/after, compaction)

These are 3 different mechanisms for 3 different purposes. Conflating them is a design error.

**SDK pattern:** `pi-mom` is a thin consumer of `pi-agent-core`. Same architecture Lyra targets with adapters consuming Hub.

**What to take:** The skills/tools/extensions distinction maps directly to Lyra — tools = ToolHandler Protocol, skills = agent TOML system prompt, extensions = hook system (Phase 1, cited in roadmap). The SDK/thin-adapter pattern validates Lyra's existing architecture.

---

### 3.4 OpenFang (Rust, Terracotta, WASM sandboxing)

**53 built-in tools by category:**

| Category | Tools | Sandbox |
|----------|-------|---------|
| Filesystem | read, write, list, patch | Path traversal prevention, sandboxed ops |
| Web | fetch, search | SSRF-protected |
| Shell | exec | Metacharacter check + subprocess sandbox |
| Code | python_repl, docker, git | Isolated execution environments |
| Inter-agent | send, spawn, list | A2A with HMAC-SHA256 auth |
| Browser | Playwright automation | Full isolation |

**MCP integration:** 25 bundled MCP servers, tool cache, automatic fallback. MCP tools traverse the same dispatch path as native tools.

**16 security layers (relevant subset for Lyra):**
1. Subprocess Sandbox — shell exec in isolated subprocess
2. Prompt Injection Scanner — detect injection in tool inputs
3. Capability Gates — tool access gated per agent
4. SSRF Protection — web fetch blocked from internal IPs
5. Path Traversal Prevention — filesystem can't escape allowed dirs
6. Loop Guard (SHA256) — detect repeated identical tool calls
7. Approval gates — sensitive ops require human sign-off
8. Taint tracking — data flow per value

**Per-category security is the key insight.** Shell ≠ Filesystem ≠ Web in terms of risk surface. A generic sandbox flag is insufficient.

**What to take:** `shell_sandboxed` = subprocess sandbox + metacharacter check, no `shell=True`, blocklist. `web_fetch` = SSRF protection (block 10.x, 192.168.x, 127.x, 169.254.x, fc00::/7). `file_read` = path traversal prevention (resolve symlinks, check against allowed root). Approval gate = separate concern.

---

## 4. Lyra's Approach

### Protocol (Layer 1)

```python
class ToolHandler(Protocol):
    name: str
    description: str
    input_schema: dict        # JSON Schema (Anthropic tool spec format)
    read_only: bool = False   # safe for parallel exec in Phase 2
    async def execute(self, input: dict) -> str: ...
```

### Registry (Layer 2)

```python
class ToolRegistry:
    def register(self, handler: ToolHandler) -> None
    def get_definitions(self) -> list[dict]    # Anthropic-format tool list
    async def dispatch(self, name: str, input: dict) -> str
```

`get_definitions()` returns the list fed directly to `anthropic.messages.create(tools=...)`. In Phase 2, it accepts a `profile` param to filter by toolset.

### The 3 First Tools (Layer 3)

**ShellTool:**
```python
class ShellTool:
    name = "shell"
    description = "Run a shell command in a sandboxed subprocess"
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Command to run"},
            "timeout": {"type": "integer", "default": 30}
        },
        "required": ["command"]
    }
    read_only = False  # state-changing

    async def execute(self, input: dict) -> str:
        # 1. Blocklist check (rm -rf, dd, mkfs, etc.) → return error string
        # 2. subprocess.run([...], shell=False, timeout=input.get("timeout", 30))
        # 3. Return stdout + stderr as string
```

**FileReadTool:**
```python
class FileReadTool:
    name = "file_read"
    description = "Read a file from the filesystem"
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"]
    }
    read_only = True

    async def execute(self, input: dict) -> str:
        # 1. Resolve path (resolve symlinks)
        # 2. Check against allowed roots (configured per agent in config.toml)
        # 3. Return file contents
```

**WebFetchTool:**
```python
class WebFetchTool:
    name = "web_fetch"
    description = "Fetch content from a URL"
    input_schema = {
        "type": "object",
        "properties": {"url": {"type": "string"}},
        "required": ["url"]
    }
    read_only = True

    async def execute(self, input: dict) -> str:
        # 1. SSRF check: block 10.x, 192.168.x, 127.x, 169.254.x, fc00::/7
        # 2. DNS resolution check (resolves before request to catch rebinding)
        # 3. aiohttp GET with timeout
        # 4. Return text content (truncated to N chars)
```

### Turn Loop Integration

```python
# In hub.py or agent_turn.py — minimal change required
tool_definitions = registry.get_definitions()

response = await llm.complete(
    messages=history,
    tools=tool_definitions   # ← new
)

if response.stop_reason == "tool_use":
    for tool_use in response.content:
        result = await registry.dispatch(tool_use.name, tool_use.input)
        history.append(ToolResultBlock(tool_use_id=tool_use.id, content=result))
    # continue turn loop → LLM called again with tool results
```

### What Each Framework Contributed

| Decision | Influenced by |
|----------|--------------|
| `execute() → str` (not exceptions) | OpenClaw typed errors → encode in return value for Phase 1 |
| JSON Schema dict (not Pydantic) | Hermes + OpenFang — Anthropic SDK takes raw dict |
| Explicit registration (not self-reg) | Simplicity over magic; self-reg = Phase 3 plugin SDK |
| `read_only` flag on Protocol | Hermes parallel safe tools → future Phase 2 optimization |
| `get_definitions()` filter param | OpenClaw profiles → future toolsets without API change |
| Approval gate = separate concern | Hermes approval.py pattern; tools don't own policy |
| Per-category security | OpenFang — shell ≠ web ≠ file in risk surface |

---

## 5. Key Decisions

| # | Decision | Options | Verdict | Why |
|---|----------|---------|---------|-----|
| D1 | Schema format | Pydantic / TypeBox / raw JSON Schema dict | **Raw dict** | Anthropic SDK takes dict directly; no extra dep; tools stay simple |
| D2 | Registration | Explicit / Self-reg at import / Decorator | **Explicit** | Self-reg requires import order discipline; only worth it when there's a plugin SDK |
| D3 | Return type | `str` / `ToolResult` typed object / Exception | **`str`** | LLM sees strings; typed returns add a conversion layer with no Phase 1 benefit |
| D4 | Error signaling | Raise exception / Return error string / Typed ToolError | **Error string** | LLM needs to see the error to recover; exceptions bypass the turn loop |
| D5 | Shell sandbox depth | None / Blocklist only / subprocess no-shell / WASM | **subprocess + blocklist** | WASM = overkill; blocklist + `shell=False` + timeout covers 95% of risk |
| D6 | File access scope | None / CWD-relative / Explicit allowed roots | **Explicit allowed roots** | CWD-relative breaks remote agents; roots configurable per agent in config.toml |
| D7 | SSRF protection | None / Blocklist private ranges / DNS resolution check | **Blocklist + DNS check** | Blocklist alone misses SSRF via DNS rebinding |
| D8 | Parallel execution | Sequential / Parallel read-only tools | **Sequential (Phase 1)** | `read_only` flag on Protocol now; parallel dispatch = Phase 2 |
| D9 | Approval gate | Inside tool / Separate approval module / LLM-visible warning | **Separate approval module** | Hermes approval.py pattern; tools don't own policy |
| D10 | MCP client | Phase 1 / Phase 2 / Phase 3 | **Phase 3** | Blocked on Plugin SDK (#478); ToolRegistry must exist first |

---

## 6. Epic + Task Slicing

### #477 Structure

```
#477 ToolRegistry (Phase 1)  ←── THIS
    ├── #477-T1: ToolHandler Protocol + ToolRegistry         (S — 1d)
    ├── #477-T2: ShellTool (sandboxed subprocess)            (S — 1d)
    ├── #477-T3: FileReadTool (path check)                   (S — 0.5d)
    ├── #477-T4: WebFetchTool (SSRF protection)              (S — 0.5d)
    ├── #477-T5: ApprovalGate module                         (S — 0.5d)
    ├── #477-T6: Hub integration (turn loop wiring)          (S — 1d)
    └── #477-T7: Tests + docs                                (S — 1d)

Unblocked by #477:
    #478 Plugin SDK (Phase 3c)        — depends on registry existing
    #480 CRON scheduler (Phase 4)     — agents need tools to act autonomously
    #481 Inter-agent (Phase 4)        — delegation requires tool dispatch

Blocked #477 by (none — this is Phase 1 work):
    Protocol formalization (#443 ChannelAdapter + LlmProvider) — already done
```

### Task Table

| ID | Slice | Size | Phase | Description | Blocks |
|----|-------|------|-------|-------------|--------|
| T1 | ToolHandler + Registry | S | 1 | Protocol definition, register/dispatch/get_definitions (~120 LOC) | T6 |
| T2 | ShellTool | S | 1 | subprocess sandbox, blocklist, timeout (~80 LOC) | T6, T5 |
| T3 | FileReadTool | S | 1 | path resolution, allowed roots (~40 LOC) | T6 |
| T4 | WebFetchTool | S | 1 | SSRF check, aiohttp, truncation (~60 LOC) | T6 |
| T5 | ApprovalGate | S | 1 | dangerous cmd detection, separate module (~40 LOC) | T6 |
| T6 | Hub wiring | S | 1 | tool_definitions inject, tool_use branch (~50 LOC) | T7 |
| T7 | Tests + docs | S | 1 | unit + integration, docstrings | — |
| #478 | Plugin SDK | F-full | 3c | SKILL.md manifest, discovery, install | #480 |
| #480 | CRON scheduler | F-full | 4 | scheduled agent tasks | #481 |
| #481 | Inter-agent | F-full | 4 | async delegation + sync collaboration | — |

**Total #477 scope:** ~300 LOC, ~5 days, zero external dependencies added.

### File Layout

```
src/lyra/tools/
    __init__.py       — ToolHandler Protocol + ToolRegistry (~120 LOC)
    shell.py          — ShellTool (~80 LOC)
    file_read.py      — FileReadTool (~40 LOC)
    web_fetch.py      — WebFetchTool (~60 LOC)
    approval.py       — ApprovalGate (~40 LOC)
```

Wiring in `src/lyra/hub.py` or `src/lyra/agent_turn.py` (~50 LOC change).
