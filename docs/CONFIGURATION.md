# Configuration Reference

Lyra uses two types of configuration files with distinct responsibilities:

- **System data** — versioned, ships with the code, defines what Lyra and its agents *are*
- **Instance config** — gitignored, per-machine, defines how THIS deployment runs

---

## Overview

| File | Type | Versioned | Purpose |
|------|------|-----------|---------|
| `config.toml` | Instance config | No | Deployment wiring: bots, tokens, auth, defaults |
| `~/.lyra/agents/<name>.toml` | User config | No | Agent override: wins over system default |
| `src/lyra/agents/<name>.toml` | System data | Yes | Agent definition: model, tools, routing |
| `src/lyra/plugins/<name>/plugin.toml` | System data | Yes | Plugin manifest: commands, handlers |
| `src/lyra/config/messages.toml` | System data | Yes | i18n strings |
| `pyproject.toml` | System data | Yes | Package metadata, dependencies, tool config |

**Rule:** if a value is machine-specific, personal, or secret → `config.toml`. Everything else → versioned.

---

## `config.toml` — Instance config (local, gitignored)

Copy from `config.toml.example` on each machine. Never commit it.

### `[defaults]` — Machine-wide fallbacks

```toml
[defaults]
cwd = "~/projects"                      # default working directory for agent subprocesses
persona = "lyra_default"               # fallback persona if agent doesn't specify one
# workspaces.lyra     = "~/projects/lyra"
# workspaces.projects = "~/projects"
```

These values are used when an agent definition does not specify them. Resolution order:

```
agents/<name>.toml value      (agent-specific, highest priority)
    ↓ fallback
config.toml [defaults]        (machine-wide default)
    ↓ fallback
hardcoded default              (cwd = "~", persona = none)
```

### `[admin]` — Admin users

```toml
[admin]
# User IDs allowed to run admin commands (/invite, /unpair, etc.)
# Format: "tg:user:<numeric_id>" or "dc:user:<numeric_snowflake>"
user_ids = [
    # "tg:user:123456789",
    # "dc:user:123456789012345678",
]
```

### `[[telegram.bots]]` / `[[discord.bots]]` — Bot instances

```toml
[[telegram.bots]]
bot_id = "lyra"
token = "env:TELEGRAM_TOKEN"           # resolved from environment at startup
bot_username = "env:TELEGRAM_BOT_USERNAME"
webhook_secret = "env:TELEGRAM_WEBHOOK_SECRET"
agent = "lyra_default"                 # must match an agents/<name>.toml

[[discord.bots]]
bot_id = "lyra"
token = "env:DISCORD_TOKEN"
auto_thread = true
agent = "lyra_default"
```

`env:VAR_NAME` values are resolved from the environment — keeps secrets out of the file.

### `[[auth.telegram_bots]]` / `[[auth.discord_bots]]` — Auth rules

```toml
[[auth.telegram_bots]]
bot_id = "lyra"
default = "blocked"                    # "blocked" | "trusted" | "owner"
owner_users = []                       # numeric Telegram user IDs
trusted_users = []                     # can interact, cannot admin

[[auth.discord_bots]]
bot_id = "lyra"
default = "blocked"
owner_users = []                       # numeric Discord snowflake IDs
trusted_roles = []                     # numeric Discord role snowflake IDs
```

At least one platform must be configured. A missing platform logs a warning and is skipped.

---

## Agent definitions — `~/.lyra/agents/` and `src/lyra/agents/`

Agent TOML files are resolved in this order:

```
~/.lyra/agents/<name>.toml     ← user-level (gitignored, machine-specific, wins)
    ↓ fallback
src/lyra/agents/<name>.toml    ← system defaults (versioned, ships with code)
```

Place a file in `~/.lyra/agents/` to override a system agent entirely, or to define a personal agent that isn't versioned. The system defaults in `src/lyra/agents/` are used when no user-level file exists.

One file per agent. Defines the agent's behavior — no machine-specific values in the versioned files.

```toml
[agent]
name = "lyra_default"
memory_namespace = "lyra"
permissions = []
persona = "lyra_default"          # loads system prompt from ~/.roxabi-vault
                                   # omit to inherit from config.toml [defaults].persona
show_intermediate = false

[model]
backend = "claude-cli"            # "claude-cli" | "anthropic-sdk" | "ollama"
model = "claude-sonnet-4-6"
max_turns = 10
# cwd and workspaces are NOT set here — they come from config.toml [defaults]
tools = ["Read", "Grep", "Glob", "WebFetch", "WebSearch"]
# Bash and Write omitted intentionally — prevents prompt injection → RCE

[agent.smart_routing]             # requires backend = "anthropic-sdk"
enabled = false

[agent.smart_routing.models]
trivial  = "claude-haiku-4-5-20251001"
simple   = "claude-haiku-4-5-20251001"
moderate = "claude-sonnet-4-6"
complex  = "claude-opus-4-6"

[plugins]
enabled = ["echo"]
```

**What belongs here:** model, tools allowlist, smart routing tiers, plugin list, persona name, memory namespace.

**What does NOT belong here:** `cwd`, `workspaces` — these are machine-specific and live in `config.toml [defaults]`.

---

## `src/lyra/plugins/<name>/plugin.toml` — Plugin manifest (versioned)

```toml
name = "echo"
description = "Echo messages back for testing"
version = "0.1.0"
priority = 100
enabled = true
timeout = 5.0

[[commands]]
name = "echo"
description = "Echo back the message (test command)"
handler = "cmd_echo"              # Python function name in handlers.py
```

---

## `src/lyra/config/messages.toml` — i18n strings (versioned)

UI strings keyed by language (`en`, `fr`). Used by the message manager for error messages and system responses.

---

## Load order

```
startup
  └── config.py: load config.toml
        ├── parse [defaults] → machine-wide fallbacks
        ├── resolve env:VAR_NAME references
        ├── parse [[telegram.bots]] / [[discord.bots]]
        └── for each bot → core/agent.py: load agent TOML
              ├── try ~/.lyra/agents/<name>.toml → fallback src/lyra/agents/<name>.toml
              ├── merge with [defaults] (cwd, persona, workspaces)
              ├── build ModelConfig, SmartRoutingConfig
              ├── load persona from vault (if set)
              └── compose system prompt → frozen Agent dataclass
```

`config.toml` is loaded once at startup. Agent definitions support hot-reload: the loader checks file mtime on each message and reloads if changed.
