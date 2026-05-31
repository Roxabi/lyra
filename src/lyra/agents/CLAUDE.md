# src/lyra/agents/ — Agent Implementations

## Contract

`AgentBase.process(msg, pool)` is the single entry point for all agents. `SimpleAgent` is the
standard implementation for `backend = "claude-cli"`.

Store/lifecycle machinery (`AgentStore` lives in `infrastructure/stores/`, `AgentSeederTarget` and `AgentRow` live in `core/`) — not here.

## Backend wiring

Two backend paths exist; exactly one is active per agent instance:

| Path | When | Key object |
|------|------|------------|
| Direct CLI | `backend = "claude-cli"`, single-process | `CliPool` |
| NATS-relayed CLI | distributed / hub-spoke | `LlmClient` (composed via `CliNatsCodec` over `WorkerPoolClient`) |

`configure_pool(pool)` wires `reset_fn / resume_fn / workspace_fn` callbacks before the first
`process()` call. Both `cli_pool` and `cli_nats_driver` register the same callbacks; whichever is
non-`None` at construction time is active. The kwarg name `cli_nats_driver` is historical (now
holds an `LlmClient | None`); rename is a deferred follow-up per #1281 W2 decision.

## Hot-reload

TOML and persona file changes are picked up on the **next** `process()` call — no daemon restart
needed for content edits. Schema changes (new fields, backend swap) still require
`lyra agent init --force` + restart.

## TOML → DB seeding rule

TOML files are **seed-only**. The runtime reads from SQLite (`~/.lyra/config.db`), never from TOML
directly.

```
~/.lyra/agents/<name>.toml   ← edit here
         ↓  lyra agent init [--force]
~/.lyra/config.db            ← runtime SSoT
```

After any TOML edit: `lyra agent init --force` + daemon restart (no file watcher).

`cwd` is machine-specific — set in `config.toml [defaults]`, NOT in agent TOML.

→ Full TOML schema and CLI reference: `docs/agent-management.md`

## Known gotcha

`_WORKSPACE_BUILTIN_CONFLICTS` (in `core/agent/agent_config.py`) — workspace keys that shadow built-in
command names are rejected at init time. Check this list before adding new workspace shortcuts.

## What NOT to do

- ¬ store/DB logic in agent files — belongs in `core/`
- ¬ read TOML at runtime from agent classes — use `AgentStore`
- ¬ hardcode model names or backend — read from `Agent.llm_config`
- ¬ set `cwd` in agent TOML
- ¬ platform-specific code in agents — adapters handle that
- ¬ enable `smart_routing` — deprecated; stored for legacy compat only
