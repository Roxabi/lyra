# CLAUDE.md — lyra.agent_cmd

## Role

CLI command implementations for `lyra agent ...` (init, list, show, edit, patch,
validate, create, delete, assign, unassign, refine). Wired via `src/lyra/agents/`
subdir and dispatched by the Typer CLI entrypoint.

## Position in the architecture

Applicative layer — sits above `core/` and may import from it directly. This is
intentional and **not** an import-linter violation. `agent_cmd` is a user-facing
CLI boundary, not a domain module; the same logic that permits `bootstrap/` to
wire everything applies here.

```
lyra CLI entrypoint
      ↓
  lyra.agent_cmd     ← you are here
      ↓
  lyra.core (stores, auth.db)
```

## Why distinct from `agents/`

| `agents/` | `agent_cmd/` |
|---|---|
| Agent implementations (SimpleAgent, …) | CLI commands that manage agents |
| Runtime behaviour | CRUD via SQLite store (`~/.lyra/auth.db`) |

## Invariants

- `lyra agent init` **must** be called before the hub can use an agent.
  `~/.lyra/auth.db` is the SSoT; TOML files are seed inputs only (¬override at runtime).
- Commands in `agent_cmd/agents/` must not bypass the store — always go through
  `AgentStore` (read/write), never directly to the TOML file.
- `agent_cmd` must not import from `adapters/`, `commands/`, or any plugin layer.
