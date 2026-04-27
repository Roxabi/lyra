# Configuration Reference

Lyra uses two types of configuration files with distinct responsibilities:

- **System data** — versioned, ships with the code, defines what Lyra and its agents *are*
- **Instance config** — gitignored, per-machine, defines how THIS deployment runs

---

## Overview

| File | Type | Versioned | Purpose |
|------|------|-----------|---------|
| `config.toml` | Instance config | No | Deployment wiring: bots, tokens, auth, defaults |
| `lyra.toml` | Instance config | No | Monitoring thresholds (read by `lyra.monitoring` only) |
| `~/.lyra/config.db` | Runtime DB | No | Agents, credentials, grants, user prefs (SQLite) |
| `~/.lyra/turns.db` | Runtime DB | No | Conversation turns, pool sessions |
| `~/.lyra/discord.db` | Runtime DB | No | Discord thread data (owned by Discord adapter) |
| `~/.lyra/auth.db` | Runtime DB | No | Auth grants, identity aliases (legacy name, still used) |
| `~/.lyra/message_index.db` | Runtime DB | No | Message index for search/retrieval |
| `~/.lyra/agents/<name>.toml` | Seed source | No | Agent seed: imported into DB by `lyra agent init` |
| `src/lyra/agents/<name>.toml` | Seed source | Yes | Agent seed: system defaults, imported into DB |
| `src/lyra/commands/<name>/plugin.toml` | System data | Yes | Plugin manifest: commands, handlers |
| `src/lyra/config/messages.toml` | System data | Yes | i18n strings |
| `pyproject.toml` | System data | Yes | Package metadata, dependencies, tool config |

**Rule:** if a value is machine-specific, personal, or secret → `config.toml`. Everything else → versioned.

**Agent rule:** TOML files define what agents *should be*. The DB holds what they *are* at runtime. Startup reads from the DB only — TOML changes require `lyra agent init` (or `--force`) to take effect.

---

## Config File Resolution

### `config.toml` — Hub and adapters

Resolution order (first match wins):

```
1. $LYRA_CONFIG           (if set, must be under $HOME)
2. $LYRA_VAULT_DIR/config.toml
3. ./config.toml          (cwd)
4. Empty dict (defaults)
```

The path is validated to be under `$HOME` when set via `LYRA_CONFIG`.

### `lyra.toml` — Monitoring only

Resolution order:

```
1. $LYRA_CONFIG           (if set, must be under $HOME)
2. ./lyra.toml            (cwd)
3. Empty dict (defaults)
```

**Note:** Hub uses `config.toml`, monitoring uses `lyra.toml`. If you set `$LYRA_CONFIG`, it must contain both `[monitoring]` and any other sections you need.

### `messages.toml` — i18n strings

Resolution order:

```
1. $LYRA_MESSAGES_CONFIG  (if set, must end in .toml and be under $HOME)
2. ./messages.toml        (cwd)
3. src/lyra/config/messages.toml  (bundled)
```

### Store directory (`~/.lyra/`)

Controlled by `LYRA_VAULT_DIR`:

```
$LYRA_VAULT_DIR  (if set)
~/.lyra          (default)
```

Databases created under this directory:

| DB File | Tables |
|---------|--------|
| `config.db` | `agents`, `bot_agent_map`, `agent_runtime_state`, `bot_secrets`, `user_prefs` |
| `auth.db` | Auth grants, identity aliases |
| `turns.db` | Conversation turns, pool sessions |
| `discord.db` | `discord_threads` (owned by Discord adapter) |
| `message_index.db` | Message index |

---

## `config.toml` Sections

### `[defaults]` — Machine-wide fallbacks

```toml
[defaults]
cwd = "~/projects"              # default working directory for agent subprocesses
persona = "lyra_default"        # fallback persona if agent doesn't specify one
workspaces.lyra = "~/projects/lyra"    # adds /lyra slash command
workspaces.projects = "~/projects"     # adds /projects slash command
```

Resolution order for agent overrides:

```
agents/<name>.toml value      (agent-specific, highest priority)
    ↓ fallback
config.toml [defaults]        (machine-wide default)
    ↓ fallback
hardcoded default              (cwd = "~", persona = none)
```

### `[agents.<name>]` — Per-agent overrides

```toml
[agents.lyra_default]
cwd = "~/projects/lyra"
persona = "dev-assistant"
workspaces.lyra = "~/projects/lyra"
```

Merged with `[defaults]` — agent-specific values win. Workspaces are deep-merged.

### `[admin]` — Admin users

```toml
[admin]
user_ids = [
    "tg:user:123456789",
    "dc:user:123456789012345678",
]
```

Format: `"tg:user:<numeric_id>"` or `"dc:user:<numeric_snowflake>"`.

### `[[telegram.bots]]` — Telegram bot instances

```toml
[[telegram.bots]]
bot_id = "lyra"
agent = "lyra_default"         # fallback if DB has no bot→agent mapping
```

Credentials (token, webhook_secret) are resolved from `CredentialStore` at bootstrap, not stored here.

### `[[discord.bots]]` — Discord bot instances

```toml
[[discord.bots]]
bot_id = "lyra"
auto_thread = true             # create thread per conversation (default: true)
agent = "lyra_default"         # fallback if DB has no bot→agent mapping
thread_hot_hours = 36          # hours before thread is considered cold (default: 36)
```

### `[[auth.telegram_bots]]` / `[[auth.discord_bots]]` — Auth rules

```toml
[[auth.telegram_bots]]
bot_id = "lyra"
default = "blocked"            # "blocked" | "trusted" | "owner"
owner_users = [123456789]      # numeric Telegram IDs — seeded into DB
trusted_users = [987654321]    # can interact, cannot admin

[[auth.discord_bots]]
bot_id = "lyra"
default = "blocked"
owner_users = [123456789012345678]
trusted_roles = [111222333444555666]  # numeric Discord role snowflakes
```

### `[hub]` — Hub configuration

```toml
[hub]
pool_ttl = 604800.0            # pool time-to-live in seconds (default: 7 days)
rate_limit = 20                # max messages per user per window (default: 20)
rate_window = 60               # rate limit window in seconds (default: 60)
```

### `[pool]` — Pool configuration

```toml
[pool]
safe_dispatch_timeout = 10.0   # timeout for safe dispatch operations (default: 10s)
```

### `[cli_pool]` — Claude CLI pool configuration

```toml
[cli_pool]
idle_ttl = 1200                # idle process TTL in seconds (default: 20 min)
default_timeout = 1200         # default turn timeout (default: 20 min)
turn_timeout = null            # optional turn timeout override
reaper_interval = 60           # reaper check interval (default: 60s)
kill_timeout = 5.0             # process kill timeout (default: 5s)
read_buffer_bytes = 1048576    # read buffer size (default: 1 MiB)
stdin_drain_timeout = 10.0     # stdin drain timeout (default: 10s)
max_idle_retries = 3           # max idle process retries (default: 3)
intermediate_timeout = 5.0     # intermediate response timeout (default: 5s)
```

### `[inbound_bus]` — Inbound message bus

```toml
[inbound_bus]
queue_depth_threshold = 100    # alert threshold for queue depth (default: 100)
staging_maxsize = 500          # staging queue max size (default: 500)
platform_queue_maxsize = 100   # per-platform queue max size (default: 100)
```

### `[debouncer]` — Message debouncing

```toml
[debouncer]
default_debounce_ms = 300      # debounce window (default: 300ms)
max_merged_chars = 4096        # max chars in merged message (default: 4096)
cancel_on_new_message = false  # cancel ongoing turn on new message (default: false)
```

### `[event_bus]` — Pipeline event bus

```toml
[event_bus]
queue_maxsize = 1000           # event queue max size (default: 1000)
```

### `[llm]` — LLM driver configuration

```toml
[llm]
max_retries = 3                # max retries on failure (default: 3)
backoff_base = 1.0             # exponential backoff base (default: 1.0)
```

### `[logging]` — Structured log output

```toml
[logging]
json_file = true               # write JSON log file (default: true)
level = "info"                 # log level (default: "info")
```

### `[message_index]` — Message retention

```toml
[message_index]
retention_days = 90            # days to retain indexed messages (default: 90)
```

### `[pairing]` — Device pairing

```toml
[pairing]
enabled = false                # enable pairing system (default: false)
alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # safe alphabet for codes
code_length = 8                # pairing code length (default: 8)
ttl_seconds = 3600             # code validity in seconds (default: 1 hour)
max_pending = 3                # max pending codes per user (default: 3)
session_max_age_days = 30      # session max age (default: 30 days)
rate_limit_attempts = 5        # rate limit attempts (default: 5)
rate_limit_window = 300        # rate limit window in seconds (default: 5 min)
```

### `[tool_display]` — Tool call display during streaming

```toml
[tool_display]
names_threshold = 3            # edits to show per file before collapsing (default: 3)
group_threshold = 3            # files before grouped summary (default: 3)
bash_max_len = 60              # max bash command chars (default: 60)
throttle_ms = 2000             # min ms between tool-summary updates (default: 2000)

[tool_display.show]
edit = true                    # show edit tool calls (default: true)
write = true                   # show write tool calls (default: true)
bash = true                    # show bash tool calls (default: true)
web_fetch = true               # show web_fetch tool calls (default: true)
web_search = true              # show web_search tool calls (default: true)
agent = true                   # show agent tool calls (default: true)
read = false                   # silent by default (high-frequency, low signal)
grep = false                   # silent by default
glob = false                   # silent by default
```

### `[circuit_breaker.<service>]` — Circuit breaker per service

```toml
[circuit_breaker.claude-cli]
failure_threshold = 5          # failures before opening (default: 5)
recovery_timeout = 60          # seconds before retry (default: 60)

[circuit_breaker.telegram]
failure_threshold = 5
recovery_timeout = 60

[circuit_breaker.discord]
failure_threshold = 5
recovery_timeout = 60

[circuit_breaker.hub]
failure_threshold = 5
recovery_timeout = 60
```

Services: `claude-cli`, `telegram`, `discord`, `hub`.

---

## `lyra.toml` — Monitoring Only

Read exclusively by `lyra.monitoring`. Hub does NOT read this file.

### `[monitoring]` — Thresholds

```toml
[monitoring]
check_interval_minutes = 5                    # timer interval (default: 5)
health_endpoint_timeout_s = 5                 # HTTP timeout (default: 5)
queue_depth_threshold = 80                    # alert threshold (default: 80)
idle_threshold_hours = 6                      # idle alert threshold (default: 6)
quiet_start = "00:00"                         # quiet period start (default: "00:00")
quiet_end = "08:00"                           # quiet period end (default: "08:00")
idle_check_enabled = false                    # enable idle checks (default: false)
min_disk_free_gb = 1                          # disk alert threshold (default: 1)
health_endpoint_url = "http://localhost:8443/health/detail"
diagnostic_model = "claude-haiku-4-5-20251001"
disk_check_path = "/"                         # filesystem to check (default: "/")
service_names = ["lyra-hub", "lyra-telegram", "lyra-discord"]
health_secret = ""                            # optional health endpoint auth
```

---

## Environment Variables

### Core paths

| Variable | Default | Description |
|----------|---------|-------------|
| `LYRA_CONFIG` | — | Path to `config.toml` (hub) or `lyra.toml` (monitoring) |
| `LYRA_VAULT_DIR` | `~/.lyra` | Store directory for all databases |
| `LYRA_MESSAGES_CONFIG` | bundled | Path to custom `messages.toml` |
| `LYRA_DB` | — | Override database path (test only) |
| `LYRA_LOG_DIR` | — | Log directory override |

### Telegram

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_TOKEN` | Yes (monitoring) | Bot token |
| `TELEGRAM_WEBHOOK_SECRET` | Yes (hub) | Webhook secret |
| `TELEGRAM_ADMIN_CHAT_ID` | Yes (monitoring) | Chat ID for alerts |
| `TELEGRAM_BOT_USERNAME` | No | Bot username for help text |

### Discord

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_TOKEN` | Yes (hub) | Bot token |
| `DISCORD_AUTO_THREAD` | No | `"true"/"1"/"yes"/"on"` to enable auto-thread (default: true) |

### NATS

| Variable | Default | Description |
|----------|---------|-------------|
| `NATS_URL` | `nats://localhost:4222` | NATS server URL (required for standalone hub) |

### Voice (STT/TTS)

| Variable | Default | Description |
|----------|---------|-------------|
| `LYRA_STT_MODEL` | `large-v3-turbo` | Whisper model size |
| `LYRA_STT_TIMEOUT` | `15` | STT timeout in seconds |
| `LYRA_TTS_ENGINE` | — | TTS engine (per-adapter in voiceCLI container) |
| `LYRA_TTS_VOICE` | — | TTS voice ID |
| `LYRA_TTS_LANGUAGE` | — | TTS language code |
| `LYRA_TTS_TIMEOUT` | — | TTS timeout in seconds |
| `LYRA_AUDIO_TMP` | — | Audio temp directory |
| `LYRA_MAX_AUDIO_BYTES` | — | Max audio file size |

### Health endpoint

| Variable | Default | Description |
|----------|---------|-------------|
| `LYRA_HEALTH_HOST` | — | Health endpoint host |
| `LYRA_HEALTH_PORT` | — | Health endpoint port |
| `LYRA_HEALTH_SECRET` | — | Health endpoint auth secret |

### Misc

| Variable | Default | Description |
|----------|---------|-------------|
| `LYRA_SUPERVISORCTL_PATH` | — | Path to supervisorctl |
| `LYRA_AGENT_STORE_PATH` | — | Override agent store path |
| `LYRA_CLAUDE_CWD` | — | Claude CLI working directory |
| `LYRA_WEB_INTEL_PATH` | — | Web intel output path |

---

## Runtime databases — `~/.lyra/`

| Database | Contents |
|----------|----------|
| `config.db` | Agents, bot-agent map, agent runtime state, credentials, user prefs |
| `turns.db` | Conversation turns, pool sessions |
| `discord.db` | Discord thread data (owned by Discord adapter) |
| `auth.db` | Auth grants, identity aliases |
| `message_index.db` | Message index for search/retrieval |
| `keyring.key` | Encryption key for credential store |

**Migration:** On first startup after upgrading from pre-v15, Lyra automatically migrates existing rows from `auth.db` to `config.db`, `turns.db`, and `discord.db`. Old `auth.db` is kept as tombstone.

---

## Agent definitions — SQLite DB + TOML seeds

Agents are stored in **`~/.lyra/config.db`** (SQLite). This is the runtime source of truth.

TOML files are **seed sources** — imported via `lyra agent init`. After import, TOML edits have no effect until re-imported.

### CLI workflow

```bash
lyra agent init              # seed DB from TOML files
lyra agent init --force      # force re-import (overwrites DB rows)
lyra agent list              # list all agents in DB
lyra agent show <name>       # show full config for one agent
lyra agent edit <name>       # edit an agent in DB interactively
lyra agent validate <name>   # schema + constraint checks
lyra agent delete <name>     # delete an agent (refuses if bot assigned)
lyra agent assign <name> --platform telegram --bot <bot_id>
lyra agent unassign --platform telegram --bot <bot_id>
```

### TOML format (seed files)

```toml
[agent]
name = "lyra_default"
memory_namespace = "lyra"
permissions = []
persona = "lyra_default"          # loads system prompt from ~/.roxabi-vault
show_intermediate = false

[model]
backend = "claude-cli"            # "claude-cli" | "nats" (future)
model = "claude-sonnet-4-6"
max_turns = 10
tools = ["Read", "Grep", "Glob", "WebFetch", "WebSearch"]

[plugins]
enabled = ["echo"]
```

**What belongs here:** model, tools allowlist, plugin list, persona name, memory namespace.

**What does NOT belong here:** `cwd`, `workspaces` — machine-specific, live in `config.toml [defaults]`.

---

## Load order summary

```
startup
  ├── _load_raw_config() → config.toml
  │     ├── $LYRA_CONFIG (validated under $HOME)
  │     ├── $LYRA_VAULT_DIR/config.toml
  │     ├── cwd/config.toml
  │     └── {} (empty → all defaults)
  │
  ├── _load_circuit_config() → [circuit_breaker.*] + [admin]
  │
  ├── load_multibot_config() → [[telegram.bots]] + [[discord.bots]]
  │     └── backward compat: [auth.telegram]/[auth.discord] → synthesize bot_id="main"
  │
  ├── _load_*_config() → individual section models
  │     ├── [hub], [pool], [cli_pool], [llm]
  │     ├── [inbound_bus], [debouncer], [event_bus]
  │     ├── [logging], [message_index], [pairing]
  │     └── [tool_display]
  │
  └── AgentStore.connect() → ~/.lyra/config.db
        └── for each bot → resolve agent from DB
              ├── bot_agent_map row (highest priority)
              └── if missing → config.toml bot.agent → auto-seed
```

---

## Monitoring (`lyra-monitor.timer`)

The health monitoring cron runs as a **systemd user timer**, separate from the Quadlet containers.

### Files

| File | Role |
|------|------|
| `deploy/lyra-monitor.service` | Systemd oneshot — runs `python -m lyra.monitoring` |
| `deploy/lyra-monitor.timer` | Triggers the service every 5 minutes |
| `lyra.toml` `[monitoring]` | Thresholds |
| `.env` | Secrets: `TELEGRAM_TOKEN`, `TELEGRAM_ADMIN_CHAT_ID` |

### Installation

```bash
make register         # installs timer + enables it (but does not start)
make monitor enable   # start the timer
make monitor status   # check timer + last run
make monitor logs     # journalctl follow
make monitor run      # trigger a manual check now
make monitor disable  # stop the timer
```

---

## Voice (optional)

Enable when running `voicecli_tts` / `voicecli_stt` via voiceCLI:

```bash
LYRA_STT_MODEL=large-v3-turbo   # faster-whisper model
```

Hub probes STT/TTS adapters at startup via NATS heartbeats. Workers are discovered dynamically — no explicit enable flag needed.
