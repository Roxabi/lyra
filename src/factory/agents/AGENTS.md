# src/factory/agents/ — Agent Implementations

## Contract

`AgentBase.process(msg, pool)` is the single entry point for all agents. `SimpleAgent` is the
standard implementation for `backend = "claude-cli"`.

Store/lifecycle machinery (`AgentStore` lives in `infrastructure/stores/`, `AgentSeederTarget` and `AgentRow` live in `core/`) — not here.

## Backend wiring

Three backend paths are supported; the active one is resolved at construction time via
`isinstance` checks against `SessionAware` / `WorkspaceAware` (both declared in
`factory.core.ports.llm`):

| Path | When | Key object | Protocols |
|------|------|------------|-----------|
| Direct CLI | `backend = "claude-cli"`, single-process | `CliPool` | `SessionAware` + `WorkspaceAware` |
| NATS-relayed CLI | distributed / hub-spoke | `LlmClient` (composed via `CliNatsCodec` over `WorkerPoolClient`) | `SessionAware` + `WorkspaceAware` |
| OmpRpc NATS job | `backend = "omp-rpc"`, hub-side job dispatch | `OmpRpcDriver` | `SessionAware` only (no `switch_cwd`) |

`SimpleAgent.__init__` resolves `_session_backend` and `_workspace_backend` once from the
`(cli_pool, cli_nats_driver)` candidate pair. All four dispatch sites (`reset_backend`,
`_maybe_register_reset`, `_maybe_register_resume`, `link_lyra_session` in `process()`) route
through these two attributes — no per-site if/elif fan-out.

`configure_pool(pool)` wires `reset_fn / resume_fn / workspace_fn` callbacks before the first
`process()` call. When `_workspace_backend` is `None` (e.g. `OmpRpcDriver`), `workspace_fn`
is passed as `None` (existing no-op path in `Pool.register_session_callbacks`).

## Hot-reload

TOML and persona file changes are picked up on the **next** `process()` call — no daemon restart
needed for content edits. Schema changes (new fields, backend swap) still require
`factory agent init --force` + restart.

## TOML → DB seeding rule

TOML files are **seed-only**. The runtime reads from SQLite (`~/.roxabi/factory/config.db`), never from TOML
directly.

```
~/.roxabi/factory/agents/<name>.toml   ← edit here
         ↓  factory agent init [--force]
~/.roxabi/factory/config.db            ← runtime SSoT
```

After any TOML edit: `factory agent init --force` + daemon restart (no file watcher).

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
