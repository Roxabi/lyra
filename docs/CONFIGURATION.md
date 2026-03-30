# Configuration Reference

Lyra uses two types of configuration files with distinct responsibilities:

- **System data** — versioned, ships with the code, defines what Lyra and its agents *are*
- **Instance config** — gitignored, per-machine, defines how THIS deployment runs

---

## Overview

| File | Type | Versioned | Purpose |
|------|------|-----------|---------|
| `config.toml` | Instance config | No | Deployment wiring: bots, tokens, auth, defaults |
| `~/.lyra/auth.db` | Runtime DB | No | Agent registry (SQLite, written by `lyra agent` CLI) |
| `~/.lyra/agents/<name>.toml` | Seed source | No | Agent seed: imported into DB by `lyra agent init` |
| `src/lyra/agents/<name>.toml` | Seed source | Yes | Agent seed: system defaults, imported into DB |
| `src/lyra/commands/<name>/plugin.toml` | System data | Yes | Plugin manifest: commands, handlers |
| `src/lyra/config/messages.toml` | System data | Yes | i18n strings |
| `pyproject.toml` | System data | Yes | Package metadata, dependencies, tool config |

**Rule:** if a value is machine-specific, personal, or secret → `config.toml`. Everything else → versioned.

**Agent rule:** TOML files define what agents *should be*. The DB holds what they *are* at runtime. Startup reads from the DB only — TOML changes require `lyra agent init` (or `--force`) to take effect.

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

## Agent definitions — SQLite DB + TOML seeds

Agents are stored in **`~/.lyra/auth.db`** (SQLite). This is the runtime source of truth — startup reads agents from the DB only.

TOML files (`src/lyra/agents/<name>.toml`, `~/.lyra/agents/<name>.toml`) are **seed sources**: they define the initial state and are imported into the DB via `lyra agent init`. After import, edits to TOML files have no effect until re-imported.

### CLI workflow

```bash
# First-time setup: seed DB from TOML files
lyra agent init

# Force re-import (overwrites existing DB rows)
lyra agent init --force

# Create a new agent interactively (writes TOML, then reminds you to init)
lyra agent create

# List all agents in DB (name, backend, model, status, source, assigned bots)
lyra agent list

# Show full config for one agent
lyra agent show lyra_default

# Edit an agent in DB interactively (blank input = keep current value)
lyra agent edit lyra_default

# Validate an agent (schema + constraint checks)
lyra agent validate lyra_default

# Delete an agent (refuses if any bot is still assigned)
lyra agent unassign --platform telegram --bot lyra
lyra agent delete lyra_default

# Assign / unassign a bot
lyra agent assign lyra_default --platform telegram --bot lyra
lyra agent unassign --platform telegram --bot lyra
```

### TOML format (seed files)

One file per agent. System defaults live in `src/lyra/agents/` (versioned). Personal overrides live in `~/.lyra/agents/` (gitignored, wins over system defaults at `init` time).

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
tools = ["Read", "Grep", "Glob", "WebFetch", "WebSearch"]
# Bash and Write omitted intentionally — prevents prompt injection → RCE
# cwd is NOT set here — machine-specific, lives in config.toml [defaults]

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

**What does NOT belong here:** `cwd`, `workspaces` — machine-specific, live in `config.toml [defaults]`.

> **Note:** `lyra agent create` is an interactive wizard that scaffolds a new agent TOML file (prompts for location: `~/.lyra/agents/` or `src/lyra/agents/`). After creating the TOML, run `lyra agent init` to import it into the DB.

---

## `src/lyra/commands/<name>/plugin.toml` — Plugin manifest (versioned)

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
        └── AgentStore.connect() → open ~/.lyra/auth.db, warm in-memory cache
              └── for each bot → resolve agent from DB
                    ├── bot_agent_map DB row (highest priority)
                    │     └── if missing → fall back to config.toml bot.agent
                    │           └── auto-seeds bot_agent_map row in DB
                    └── agent row loaded from agents table
                          ├── merge with config.toml [defaults] (cwd, persona, workspaces)
                          ├── build ModelConfig, SmartRoutingConfig
                          ├── load persona from vault (if set)
                          └── compose system prompt → frozen Agent dataclass
```

`config.toml` is loaded once at startup. Agent DB rows are loaded into cache at connect time and served synchronously — no per-message file I/O.

> **Important:** TOML file changes do NOT take effect at runtime. Run `lyra agent init --force` and restart to pick up TOML edits. Use `lyra agent edit` to change a running agent without touching TOML.

## Monitoring (`lyra-monitor.timer`)

The health monitoring cron runs as a **systemd user timer**, separate from supervisor.

### Files

| File | Role |
|------|------|
| `deploy/lyra-monitor.service` | Systemd oneshot — runs `python -m lyra.monitoring` |
| `deploy/lyra-monitor.timer` | Triggers the service every 5 minutes |
| `lyra.toml` `[monitoring]` | Thresholds (queue depth, idle hours, disk, model, etc.) |
| `.env` | Secrets: `TELEGRAM_TOKEN`, `ANTHROPIC_API_KEY`, `TELEGRAM_ADMIN_CHAT_ID` |

### Installation

```bash
make register     # installs timer + enables it (but does not start)
make monitor enable   # start the timer
make monitor status   # check timer + last run
make monitor logs     # journalctl follow
make monitor run      # trigger a manual check now
make monitor disable  # stop the timer
```

### Required `.env` variables

```bash
TELEGRAM_TOKEN=bot...           # for sending alerts (same bot or dedicated)
ANTHROPIC_API_KEY=sk-ant-...    # for LLM diagnosis (Layer 2)
TELEGRAM_ADMIN_CHAT_ID=123...   # numeric chat ID for alerts
```

### Why systemd timer, not supervisor?

Monitoring is a **periodic task** (run checks, report, exit) — not a long-running daemon. Systemd timers provide `Persistent=true` (catch up after reboot), precise scheduling, and journalctl integration. The sleep-loop supervisor approach was replaced because it had no scheduling guarantees and no visibility into when the last check ran.
