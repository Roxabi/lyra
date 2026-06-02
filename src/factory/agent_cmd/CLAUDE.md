# CLAUDE.md — lyra.agent_cmd

## Role

CLI command implementations for `lyra agent ...` and `lyra bot ...`.
Verb list: `docs/agent-management.md`.
Wired via `src/factory/agent_cmd/agents/`, `src/factory/agent_cmd/bots/`, and
`src/factory/agent_cmd/platforms/` subdirs; dispatched by the Typer CLI entrypoint.

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
  lyra.core (stores, config.db [agents/bots/prefs], auth.db [grants only])
```

## Invariants

- `lyra agent init` **must** be called before the hub can use an agent.
  `~/.lyra/config.db` is the SSoT for agents; TOML files are seed inputs only (¬override at runtime).
  `~/.lyra/auth.db` holds grants and identity only (AuthStore — separate DB).
- `lyra bot init` seeds bot configurations from `config.toml` into `BotStore` (`~/.lyra/config.db`);
  idempotent by default; `--force` overwrites existing rows.
- Commands in `agent_cmd/agents/` and `agent_cmd/bots/` must not bypass their respective
  stores — always go through `AgentStore` / `BotStore` (read/write), never directly to the TOML file.
- `agent_cmd` must not import from `adapters/`, `commands/`, or any plugin layer.
