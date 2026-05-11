---
title: Agents and Plugins — Lyra
description: Standards for authoring agents, TOML configuration, plugin commands, and the agent lifecycle in Lyra.
---

# Agents and Plugins — Lyra

> Status: LIVING
> Scope: `src/lyra/agents/`, `src/lyra/commands/`, `src/lyra/core/agent/`
> Source: `src/lyra/agents/CLAUDE.md`, `src/lyra/commands/CLAUDE.md`

---

## Agents

### What an agent is

An agent is a class that implements `AgentBase` (defined in `core/agent.py`) and wires up an `LlmProvider` to handle incoming messages. `AgentBase` provides `CommandRouter`, `CommandLoader` setup, `SessionManager` mixin (context compaction, session resume), and hot-reload support.

`SimpleAgent` (`agents/simple_agent.py`) is the standard implementation. Use it for all `backend = "claude-cli"` agents.

### Agent store

Agent config is stored in SQLite (`~/.lyra/config.db`). TOML files are **seed sources only** — the runtime reads from the DB, not TOML.

```
~/.lyra/agents/<name>.toml   ← user overrides (gitignored, machine-specific)
         ↓  lyra agent init [--force]
~/.lyra/config.db            ← runtime source of truth
```

After editing any TOML file:
1. `lyra agent init --force` — re-seed the DB.
2. Restart the daemon.

The DB is NOT updated automatically on file change. There is no file watcher.

### TOML structure

```toml
[agent]
name = "lyra_default"          # unique; ^[a-zA-Z0-9_-]+$
memory_namespace = "lyra"      # memory isolation key
persona = "lyra_default"       # persona file name (without .md)
show_intermediate = true       # show ⏳ intermediate tool-use turns

[model]
backend = "claude-cli"         # "claude-cli" is the only active backend
model = "claude-sonnet-4-6"    # model identifier passed to the backend
tools = ["Read", "Grep"]       # allowed tools (empty = backend defaults)
skip_permissions = true        # skip Claude Code permission prompts

# max_turns = 10               # cap agentic turns (None/omit = unlimited)

[agent.smart_routing]
enabled = false                # MUST be false — smart_routing is deprecated

[plugins]
enabled = ["echo", "search"]   # plugin names to enable for this agent

[tts]
voice = "Sohee"

[workspaces]
lyra = "~/projects/lyra"       # /workspace lyra → switches cwd
```

**`cwd` does NOT go in agent TOML** — it is machine-specific and belongs in `config.toml [defaults]`.

**`smart_routing.enabled` must be `false`** — the validator rejects `true` on all backends.

**`workspaces` keys must not conflict** with built-in command names — see `_WORKSPACE_BUILTIN_CONFLICTS` in `core/agent_config.py`.

### Agent lifecycle

```
1. Startup:  AgentStore.connect() → lyra agent init → DB seeded
2. Register: hub.register_agent(agent)
3. Message:  PoolManager.get_or_create_pool() → pool.submit(msg)
             → agent.handle(msg, pool)
             → LlmProvider.complete() or .stream()
4. Hot-reload: TOML/persona edits detected on next handle() call
```

### CLI commands

```bash
lyra agent list                     # list all agents in DB
lyra agent show <name>              # show agent config
lyra agent init                     # seed DB from TOML files (idempotent)
lyra agent init --force             # overwrite existing DB entries
lyra agent edit <name>              # open TOML in $EDITOR
lyra agent create                   # interactive wizard
lyra agent delete <name>            # remove from DB
lyra agent assign <name> --bot telegram:main  # assign to bot
```

---

## Plugin Commands

### Plugin structure

Each plugin is a subdirectory under `src/lyra/commands/` with two files:

```
commands/
  mycommand/
    plugin.toml    # manifest
    handlers.py    # async handler functions
```

**Current plugins:** `add_vault`, `echo`, `identity`, `pairing`, `search`, `svc`.

### plugin.toml format

```toml
name = "mycommand"
description = "What this plugin does"
version = "0.1.0"
priority = 100        # lower = higher priority (affects load order)
enabled = true
timeout = 30.0        # per-handler timeout in seconds

[[commands]]
name = "mycommand"
description = "Description for /help output"
handler = "cmd_mycommand"   # function name in handlers.py
```

### Handler signatures

```python
# Plugin command handler (registered via plugin.toml)
async def cmd_mycommand(msg: InboundMessage, pool: Pool, args: list[str]) -> Response:
    return Response(content="result text")

# Session command handler (via agent.register_session_command())
async def cmd_mycommand(
    msg: InboundMessage,
    driver: LlmProvider,
    tools: SessionTools,
    args: list[str],
    timeout: float,
) -> Response:
    return Response(content="result text")
```

Always return `Response(content=...)`. Never return `None`. Never raise from a handler.

### Command routing order

```
1. Built-in commands  (/help /stop /circuit /config /clear /new /folder /workspace)
2. Session commands   (registered via register_session_command())
3. Plugin commands    (discovered from commands/ subdirs)
```

Built-in commands always win. Plugin commands cannot override built-ins.

### Admin restriction

Built-in commands use `require_admin(msg)`. For plugin commands that need admin access, check explicitly:

```python
async def cmd_mycommand(msg: InboundMessage, pool: Pool, args: list[str]) -> Response:
    if not msg.is_admin:
        return Response(content="Admin only.")
    # ... proceed
```

`msg.is_admin` is set by `Authenticator` middleware — trust it.

### Plugin enablement

Plugins are enabled per-agent in agent TOML:

```toml
[plugins]
enabled = ["echo", "search", "mycommand"]
```

A plugin not listed in `enabled` is discovered but not registered for that agent.

---

## Conventions

- One TOML file per agent. File name must equal agent `name` field (e.g. `lyra_default.toml`).
- One subdirectory per plugin. Directory name must match plugin `name` in `plugin.toml`.
- Agent names must match `^[a-zA-Z0-9_-]+$` (validated by `agent_seeder.py`).
- Command names must be lowercase alphanumeric + hyphens. No spaces.
- Handlers must be `async`. Synchronous handlers are not supported.
- Keep handlers stateless. Any persistent state belongs in a store (in `core/`).

---

## What NOT to do

- Do NOT add store or DB logic to agent implementation files — that belongs in `core/`.
- Do NOT read TOML files at runtime from within agent classes — use `AgentStore`.
- Do NOT hardcode model names in agent classes — read from `Agent.llm_config`.
- Do NOT set `cwd` in agent TOML — it belongs in `config.toml`.
- Do NOT enable `smart_routing` — the validator rejects it.
- Do NOT add LLM calls to plugin handlers — that is the agent's responsibility.
- Do NOT import from `adapters/` inside a command handler — commands are platform-agnostic.
- Do NOT use `async with channel.typing()` — it causes 429s (see `backend-patterns.md`).
- Do NOT block the event loop in a handler — all I/O must be `await`-ed.
- Do NOT hardcode platform-specific formatting in handlers — return `Response(content=plain_text)`.

---

## AI Quick Reference

ALWAYS return `Response(content=...)` from command handlers — never `None`, never raise.

ALWAYS run `lyra agent init --force` after editing a TOML file, then restart the daemon.

ALWAYS set `[agent.smart_routing] enabled = false` — the validator rejects `true`.

ALWAYS check `msg.is_admin` for admin-gated plugin commands.

NEVER put `cwd` in agent TOML — it is machine-specific and belongs in `config.toml`.

NEVER import from `adapters/` in a command handler — handlers are platform-agnostic.

NEVER block the event loop in a handler — all I/O must be `await`-ed.

NEVER create a plugin named after a built-in command (`help`, `stop`, `circuit`, `config`, `clear`, `new`, `workspace`, `folder`).

PREFER `SimpleAgent` for new agents unless you need a custom `AgentBase` implementation.

PREFER session commands (via `register_session_command`) when a handler needs injected service dependencies (driver, vault access).
