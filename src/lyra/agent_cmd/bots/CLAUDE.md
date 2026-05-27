# CLAUDE.md — lyra.agent_cmd.bots

## Role

CLI command implementations for `lyra bot ...` (init). Wired via
`src/lyra/agent_cmd/bots/` subdir and dispatched by the Typer CLI entrypoint.

## Position in the architecture

Applicative layer — sits above `core/` and may import from it directly. Same
boundary rationale as `agent_cmd/agents/`: user-facing CLI, not a domain module.

```
lyra CLI entrypoint
      ↓
  lyra.agent_cmd
      ↓
  lyra.agent_cmd.bots   ← you are here
      ↓
  lyra.core (stores)
      ↓
  BotStore (~/.lyra/config.db)
```

## Invariants

- `lyra bot init` seeds bot configurations from `config.toml` into `BotStore` (`~/.lyra/config.db`);
  idempotent by default; `--force` overwrites existing rows.
- Commands in `agent_cmd/bots/` must not bypass the store — always go through
  `BotStore` (read/write), never directly to the TOML file.
- `agent_cmd/bots/` must not import from `adapters/`, `commands/`, or any plugin layer.
- TOML files are seed inputs only (¬override at runtime). `config.db` is the SSoT.
