# src/lyra/commands/ — Plugin Commands

## Purpose

`commands/` contains Lyra's built-in plugin packages. Each subdirectory is a
self-contained plugin with a `plugin.toml` manifest and a `handlers.py` module.

Core command infrastructure (routing, loading, built-in builtins) lives in `core/`,
not here. This directory is specifically for **plugin-style commands** that are
discovered and loaded dynamically.

## Plugin structure

Each plugin lives in its own subdirectory:

```
commands/
  echo/
    plugin.toml    # manifest: name, description, version, commands[]
    handlers.py    # async handler functions
  search/
    plugin.toml
    handlers.py
  pairing/
    plugin.toml
    handlers.py
  svc/
    plugin.toml
    handlers.py
```

## plugin.toml manifest format

```toml
name = "echo"
description = "Echo a message back"
version = "0.1.0"
priority = 100        # lower = higher priority (affects load order)
enabled = true
timeout = 30.0        # per-handler timeout in seconds

[[commands]]
name = "echo"         # slash command name (without /)
description = "Echo the given text"
handler = "cmd_echo"  # function name in handlers.py
```

If a plugin registers no `[[commands]]` entries but still needs to be loaded
(e.g. to register a session command via `register_session_command`), leave
`commands = []` or omit the `[[commands]]` sections. See `search/plugin.toml`.

## Handler signatures

Two types of handlers exist depending on how they are registered:

### Plugin command handler (via plugin.toml `[[commands]]`)
```python
async def cmd_example(msg: InboundMessage, pool: Pool, args: list[str]) -> Response:
    ...
```
Receives the current `Pool` for history access or pool manipulation.

### Session command handler (via `agent.register_session_command()`)
```python
async def cmd_example(
    msg: InboundMessage,
    driver: LlmProvider,
    tools: SessionTools,
    args: list[str],
    timeout: float,
) -> Response:
    ...
```
Receives `LlmProvider` and `SessionTools` (vault access, etc.) via DI.
Session commands are used when a command needs injected service dependencies.

## Command routing order

`CommandRouter` dispatches in this priority order:
1. **Built-in commands** (`/help`, `/stop`, `/circuit`, `/routing`, `/config`,
   `/clear`, `/new`, `/folder`, `/workspace`) — always available, admin-gated where noted
2. **Session commands** — registered by agents via `register_session_command()`
3. **Plugin commands** — discovered from `commands/` subdirectories via `CommandLoader`
4. **Processor commands** — registered via `processor_registry.py` (issue #363)

Built-in commands always win. Plugin commands cannot override built-ins.

## Slash command format

Users send `/commandname arg1 arg2`. The router strips the leading `/` and
splits on whitespace to produce `args: list[str]`.

Command names must be lowercase alphanumeric + hyphens. No spaces in names.

## Plugin enablement

Plugins are enabled per-agent in the agent TOML:
```toml
[plugins]
enabled = ["echo", "search"]
```

A plugin not listed in `enabled` is discovered but not registered for that agent.

## Guards / admin restriction

Built-in commands use `require_admin(msg)` from `core/builtin_commands.py`:
```python
if (denied := require_admin(msg)):
    return denied
```

Plugin commands do not have a built-in admin guard — implement it yourself if
the command requires admin access. Read `msg.is_admin` (set by `Authenticator`).

## Conventions

- One subdirectory per plugin. Subdirectory name = plugin name (must match `name`
  in `plugin.toml`).
- Handler functions must be `async`. Synchronous handlers are not supported.
- Always return `Response(content=...)` — never return `None` or raise from a handler.
- Keep handlers stateless. Any persistent state belongs in a store (in `core/`).
- `timeout` in `plugin.toml` is enforced by the router — design handlers to
  complete well within the configured timeout.

## What NOT to do

- Do NOT add LLM calls to plugin handlers — that is the agent's responsibility.
- Do NOT import from `adapters/` inside a command handler — commands are
  platform-agnostic.
- Do NOT create plugin names that conflict with built-in command names (`help`,
  `stop`, `circuit`, `routing`, `config`, `clear`, `new`, `workspace`, `folder`).
- Do NOT block the event loop in a handler — all I/O must be `await`-ed.
- Do NOT register the same command name in both `plugin.toml` and via
  `register_session_command()` — the router will use the built-in/session version.
- Do NOT hardcode platform-specific formatting (Markdown, HTML) in handlers —
  use `Response(content=plain_text)` and let the adapter format it.
