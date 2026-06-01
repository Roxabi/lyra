# src/lyra/commands/ — Plugin Commands

## Purpose

Plugin-style commands discovered and loaded dynamically. Core routing/loading infrastructure lives in `core/`, not here.

## Command routing order

`CommandRouter` dispatches in strict priority:
1. **Built-in commands** — always win; see `core/commands/command_config.py` for the full registry
2. **Session commands** — registered by agents via `register_session_command()` (deprecated → prefer processor commands, ADR-031)
3. **Plugin commands** — discovered from `commands/` subdirectories via `CommandLoader`

Plugin commands cannot override built-ins.

**Processor commands** (`processor_registry.py`) are pre/post hooks injected into the pool flow — invoked by the pool processor, not `CommandRouter.dispatch()`. They appear in `/help` output but follow a different contract.

## plugin.toml

`plugin.toml` schema: see any existing plugin (e.g. `commands/echo/`). Leave `[[commands]]` empty (or omit) when a plugin only registers session commands — see `search/plugin.toml`.

## Guards / admin restriction

Built-in commands use `require_admin(msg)` from `core/commands/builtin_commands.py`:
```python
if (denied := require_admin(msg)):
    return denied
```
Plugin commands have no built-in guard — check `msg.is_admin` yourself.

## Conventions

- Subdirectory name = plugin name (must match `name` in `plugin.toml`)
- Handlers must be `async`; always return `Response(content=...)` — never `None`, never raise
- Keep handlers stateless — persistent state belongs in a store (`core/`)
- Plugin enablement per-agent: `[plugins] enabled = ["echo", "search"]` in agent TOML

## What NOT to do

- Do NOT add LLM calls to plugin handlers — that is the agent's responsibility
- Do NOT import from `adapters/` — commands are platform-agnostic
- Do NOT conflict with built-in command names — see `core/commands/command_config.py`
- Do NOT block the event loop — all I/O must be `await`-ed
- Do NOT hardcode platform-specific formatting — use `Response(content=plain_text)`, let the adapter format it
