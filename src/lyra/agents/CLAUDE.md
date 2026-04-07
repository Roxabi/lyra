# src/lyra/agents/ — Agent Implementations and Default TOML Configs

## Purpose

`agents/` contains **concrete agent implementations** — classes that implement `AgentBase` and
wire up an `LlmProvider` to handle incoming messages.

Note: TOML seed files are not versioned here. They live in `~/.lyra/agents/` (machine-specific,
gitignored) and are the source of truth for agent config at runtime.

Note: `AgentStore`, `AgentSeeder`, `AgentRow`, and all store/lifecycle machinery
live in `core/`, not here.

## Agent implementations

| Class | File | Backend |
|-------|------|---------|
| `SimpleAgent` | `simple_agent.py` | Any `LlmProvider` (default: `ClaudeCliDriver`) |
| `AnthropicAgent` | `anthropic_agent.py` | `AnthropicSdkDriver` only |

Both extend `AgentBase` (defined in `core/agent.py`). `AgentBase` provides:
- `CommandRouter` and `CommandLoader` setup
- `SessionManager` mixin (context compaction, session resume)
- Hot-reload support: TOML + persona file changes are picked up on next message

**SimpleAgent** is the standard agent for `backend = "claude-cli"`. It supports
streaming via `ClaudeCliDriver` and handles STT transcription and TTS synthesis.

**AnthropicAgent** is for `backend = "anthropic-sdk"`. It returns a complete
`Response` (no streaming) and passes conversation history via the Messages API.

## TOML → DB seeding flow

TOML files are **seed sources only**. The runtime reads agent config from SQLite
(`~/.lyra/auth.db`), not from TOML directly.

```
~/.lyra/agents/<name>.toml   ←  user overrides (gitignored, machine-specific)
         ↓  lyra agent init [--force]
~/.lyra/auth.db              ←  runtime source of truth
```

After editing any TOML file, run `lyra agent init --force` and restart the daemon.
The DB is NOT updated automatically on file change.

## TOML config fields

Key sections in an agent TOML:

```toml
[agent]
name = "lyra_default"          # unique identifier, used in CLI and DB
memory_namespace = "lyra"      # memory isolation key
permissions = []               # future: permission flags
persona = "lyra_default"       # persona file name (without .md)
show_intermediate = true       # show ⏳ intermediate tool-use turns

[model]
backend = "claude-cli"         # "claude-cli" | "anthropic-sdk" | "ollama" (future)
model = "claude-sonnet-4-6"    # model identifier passed to the backend
tools = ["Read", "Grep", ...]  # allowed tools (empty = backend defaults)
skip_permissions = true        # skip Claude Code permission prompts (claude-cli only)
# max_turns = 10               # cap agentic turns (None/omit = unlimited)

[agent.smart_routing]
enabled = false                # only works with backend = "anthropic-sdk"

[plugins]
enabled = ["echo", "search"]   # plugin names to enable for this agent

[tts]
voice = "Sohee"
personality = "..."

[workspaces]
lyra = "~/projects/lyra"      # /workspace lyra → switches cwd to ~/projects/lyra
```

`cwd` (working directory for the Claude subprocess) is machine-specific and lives
in `config.toml [defaults]`, NOT in agent TOML.

## Agent lifecycle

1. Startup: `AgentStore.connect()` → `lyra agent init` seeds TOML → DB
2. Hub: `hub.register_agent(agent)` makes the agent available for routing
3. Message arrives: `PoolManager.get_or_create_pool()` → `pool.submit(msg)` →
   `agent.handle(msg, pool)` → `LlmProvider.complete()` or `.stream()`
4. Hot-reload: TOML/persona edits are detected on next `handle()` call

## Conventions

- One TOML file per agent. File name = agent name (e.g. `lyra_default.toml`).
- TOML edits require `lyra agent init --force` + daemon restart — there is no
  file watcher.
- `workspaces` keys must not conflict with built-in command names (see
  `_WORKSPACE_BUILTIN_CONFLICTS` in `core/agent_config.py`).
- Agent names must match `^[a-zA-Z0-9_-]+$` (validated by `agent_seeder.py`).
- The `[prompt]` section (`system = "..."`) is an optional raw override. When set,
  it replaces persona file composition entirely.

## What NOT to do

- Do NOT add store or DB logic to agent implementation files — that belongs in `core/`.
- Do NOT read TOML files at runtime from within agent classes — use `AgentStore`.
- Do NOT hardcode model names or backend selection in agent classes — read from
  `Agent.llm_config` (populated from DB/TOML).
- Do NOT set `cwd` in agent TOML — it is machine-specific and belongs in `config.toml`.
- Do NOT add platform-specific code to agent implementations — adapters handle that.
- Do NOT enable smart routing with `backend = "claude-cli"` — unsupported combination.
