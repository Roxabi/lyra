# Agent Management

Agents live in **`~/.lyra/auth.db`** (SQLite). TOML files are seed sources only — they must be imported into the DB via `lyra agent init` before startup uses them.

## TOML Search Locations

Precedence order:

1. `~/.lyra/agents/` — user-level overrides (machine-specific, gitignored)
2. `src/lyra/agents/` — bundled system defaults

## Commands

```bash
lyra agent init           # seed DB from TOML files (first-time or after TOML edits)
lyra agent init --force   # overwrite existing DB rows
lyra agent list           # list all agents in DB
lyra agent show <name>    # full config for one agent
lyra agent edit <name>    # edit interactively in DB (no TOML needed)
lyra agent validate <name>
lyra agent create <name>                       # scaffold a new agent TOML
lyra agent assign <name> --platform telegram --bot <bot_id>
lyra agent unassign --platform telegram --bot <bot_id>
lyra agent delete <name>                       # refuses if bot still assigned
```

## Workspaces & cwd

- `cwd` is machine-specific → lives in `config.toml [defaults]`, **NOT** in agent TOML.
- `workspaces` — each key becomes accessible via `/workspace <key>` (not `/<key>`), overriding cwd for the current pool. See `docs/COMMANDS.md`.
- TOML edits → `lyra agent init --force` + restart to take effect.
