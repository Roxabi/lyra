# src/lyra/core/commands/ — Internal Command Routing Infrastructure

## Purpose

Internal command routing infrastructure — discovers plugins, parses message text into
commands, maintains the command registry, and dispatches to handlers.

## Critical distinction

| Path | What it is |
|------|-----------|
| `src/lyra/core/commands/` | **This dir** — internal routing infra (loader, parser, registry, router) |
| `src/lyra/commands/` | **Plugin commands** — user-facing handlers (echo, pairing, search, svc, …) |

Do NOT add user-facing command handlers here. This package is plumbing, not commands.

## Files

| File | Responsibility |
|------|---------------|
| `command_loader.py` | `CommandLoader` — discovers and loads plugin command handlers from TOML manifests |
| `command_parser.py` | `CommandParser` — parses message text into a `CommandContext` (name + args) |
| `command_registry.py` | `CommandRegistry` — registry of all loaded commands, keyed by name |
| `command_router.py` | `CommandRouter` — dispatches a `CommandContext` to the appropriate handler |

## Import pattern

```python
# Subpackage re-exports (preferred)
from lyra.core.commands import CommandRouter, CommandLoader

# Direct module imports
from lyra.core.commands.command_router import CommandRouter
from lyra.core.commands.command_loader import CommandLoader
from lyra.core.commands.command_registry import CommandRegistry
from lyra.core.commands.command_parser import CommandParser, CommandContext
```

## Gotchas

- `builtin_commands.py` and `workspace_commands.py` are in `core/` (flat), not here.
  This package is the routing infrastructure; built-in handlers live at the `core/` level.
- The plugin commands that users actually invoke (e.g. `/echo`, `/pair`, `/search`)
  live in `src/lyra/commands/` — a completely separate top-level package.
- `CommandLoader` reads TOML plugin manifests; it does not auto-discover Python modules.
  New plugin commands must be declared in a manifest before they appear in the registry.
