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
| `src/lyra/data/messages.toml` | System data | Yes | i18n strings |
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
3. src/lyra/data/messages.toml  (bundled)
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

Credentials (token, webhook_secret) are read from Podman secrets at bootstrap — see `## Bot credentials`.

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

## Bot credentials

Bot tokens and webhook secrets are stored as **Podman secrets**, not in `~/.lyra/config.db`. Adapter containers mount these via `Secret=` directives in `deploy/quadlet/lyra-<platform>.container`; the adapter process reads each token at bootstrap from `/run/secrets/bot_token-<bot_id>` (and optionally `/run/secrets/bot_webhook-<bot_id>`).

### CLI

| Command | Purpose |
|---|---|
| `lyra bot secret install <platform> <bot_id> [--from-env TOK] [--webhook-from-env WHK]` | Create or replace a bot's token (and optional webhook secret) |
| `lyra bot secret rm <platform> <bot_id>` | Remove the bot's token + webhook secret |
| `lyra bot secret list` | List provisioned bot secrets (filtered by `lyra-bot-` prefix) |

`<bot_id>` must match `^[A-Za-z0-9_-]+$` (alphanumeric, hyphen, underscore — slash-free for safe Podman secret names and tmpfs mount targets).

### Quadlet wiring

Each bot expects one `Secret=` line per credential in the appropriate `.container` file:

```
Secret=lyra-bot-telegram-<bot_id>,type=mount,target=bot_token-<bot_id>,mode=0400,uid=1500,gid=1500
Secret=lyra-bot-telegram-<bot_id>-webhook,type=mount,target=bot_webhook-<bot_id>,mode=0400,uid=1500,gid=1500
```

(Omit the webhook line if the bot does not use webhooks.)

Regenerate the per-platform fragments from currently-provisioned secrets:

```bash
make quadlet-bot-secrets-render
# Writes deploy/quadlet/.bot-secrets.telegram.fragment + .bot-secrets.discord.fragment
# and prints the Secret= lines to stdout. Paste each fragment into the matching
# .container file (lyra-telegram.container, lyra-discord.container).
```

After editing `.container` files, restart the adapter to remount: `make telegram-adapter restart` (or `discord-adapter`). `type=mount` secrets are tmpfs binds; `podman secret create --replace` updates the store but the in-container file is stale until container restart.

### Migrating from pre-#1057 `bot_secrets` rows

Operators on M₁ with a pre-existing `~/.lyra/config.db` `bot_secrets` table run the one-shot migration script — it reads each row, decrypts via the existing Fernet keyring, and provisions a Podman secret per `(platform, bot_id)`. The script is self-contained (it does NOT depend on the deleted `CredentialStore` class) and idempotent:

```bash
python3 tools/migrate_bot_secrets_to_podman.py            # apply
python3 tools/migrate_bot_secrets_to_podman.py --dry-run  # preview
```

After migration: regenerate the Quadlet fragments (`make quadlet-bot-secrets-render`), paste them into the matching `.container` file, restart the adapters, and optionally drop the now-orphan `bot_secrets` table (`sqlite3 ~/.lyra/config.db 'DROP TABLE bot_secrets'`). The script prints the same post-migration checklist on success.

### Rationale

webhook_secret packing: separate secret (not packed into JSON). This matches the project's raw-bytes single-purpose convention (every other Podman secret in `Makefile:181-200`), keeps the failure-loud bootstrap path free of a JSON parser, and supports independent rotation of token vs. webhook.

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

### Telegram

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_TOKEN` | No (legacy single-bot path only; multi-bot production uses Podman secrets — see `## Bot credentials`) | Bot token |
| `TELEGRAM_WEBHOOK_SECRET` | Yes (hub) | Webhook secret |
| `TELEGRAM_ADMIN_CHAT_ID` | No (legacy single-bot path only; see #1035) | Chat ID for alerts |
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

#### JetStream persistent storage

JetStream is enabled via the config file stanza in `deploy/nats/nats-container.conf` (the `-js` CLI flag was removed in #1055). Storage is backed by a Quadlet bind-mount volume:

| Unit | Host path | Container path |
|------|-----------|----------------|
| `lyra-jetstream.volume` | `~/.lyra/nats/jetstream` | `/var/lib/nats/jetstream` |

**First-time setup (production, uid 1500):**

```bash
make quadlet-install                          # creates ~/.lyra/nats/jetstream at mode 0700
podman unshare chown 1500:1500 ~/.lyra/nats/jetstream
make quadlet-secrets-install                  # skip if secrets already installed
systemctl --user restart lyra-nats
```

**Dev (no fixed uid mapping):** `make quadlet-install` is sufficient — omit the `podman unshare chown` step.

**Upgrade:** after `make quadlet-install`, run `systemctl --user daemon-reload && systemctl --user restart lyra-nats` to pick up unit file changes.

**Lint:** before deploying, validate all Quadlet unit files locally:

```bash
make quadlet-lint
```

Runs `podman quadlet --dryrun` (parse errors) and a comment-guard that rejects inline `#` comments on value lines — Quadlet does not strip them and Podman receives the literal text as a mount-option string (incident 2026-05-06, issue #1083). CI enforces the same check on every PR that touches `deploy/quadlet/`.

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

## `make quadlet-install` — deploy-time verification

`make quadlet-install` does more than copy files.  After copying all
`.network`, `.volume`, and `.container` files to `~/.config/containers/systemd/`
it runs `deploy/quadlet-install-verify.sh`, which:

1. Runs `systemctl --user daemon-reload` — triggers the Quadlet generator to
   produce fresh `.service` units from the copied files.
2. Restarts (or starts) each container unit: `lyra-nats`, `lyra-hub`,
   `lyra-telegram`, `lyra-discord`, `lyra-clipool`.
3. Waits up to 10 s per unit and checks `systemctl --user is-active`.
4. If any unit is not `active`, dumps the last 20 lines of
   `journalctl --user -u <unit>` and exits non-zero — the deploy fails loudly.

This means a broken Quadlet file (e.g. an inline `#` comment on a `Volume=`
line, which was the root cause of the 2026-05-06 incident) is caught immediately
at deploy time rather than lying dormant until the next reboot.

### Escape hatch — `NO_RESTART=1`

```bash
make quadlet-install NO_RESTART=1
```

Skips steps 1-4 (daemon-reload, restart, and verification).  Only the file
copy runs.  Use this when:

- Performing a manual recovery where one or more units are intentionally not
  running (e.g. after an nkey rotation before new seeds are in place).
- Deploying on a host that does not yet have the full secrets set up (initial
  bootstrap before `~/.lyra/env/` files exist).

After fixing the underlying issue, run a normal `make quadlet-install` (without
`NO_RESTART=1`) to verify all units come up.


---

## Monitoring — DEPRECATED (#1035)

The host-timer health monitor (`lyra-monitor.{service,timer}` + `src/lyra/monitoring/`) is **deprecated**. It pokes `systemctl --user`, `podman logs`, and host loopback ports — none of which translate cleanly to a containerised world — and offers no UI beyond a Telegram message.

It is being replaced by **Monitoring v2** — a NATS event stream + Tauri desktop dashboard — tracked in [#1035](https://github.com/Roxabi/lyra/issues/1035). Banners on the deprecated files retain the existing check logic so the v2 spec author can mine it.

For ad-hoc hub-health probes, hit `/health/detail` directly:

```bash
curl -fsS -H "Authorization: Bearer $LYRA_HEALTH_SECRET" \
  http://127.0.0.1:8443/health/detail | jq .
```

---

## Voice (optional)

Enable when running `voicecli_tts` / `voicecli_stt` via voiceCLI:

```bash
LYRA_STT_MODEL=large-v3-turbo   # faster-whisper model
```

Hub probes STT/TTS adapters at startup via NATS heartbeats. Workers are discovered dynamically — no explicit enable flag needed.

---

## WorkerError error codes

All valid error codes and their `domain`, `retryable`, and `description` fields are documented in `packages/roxabi-contracts/docs/error-codes.md`. The Markdown is **auto-generated** from `KNOWN_CODES` in `packages/roxabi-contracts/src/roxabi_contracts/errors.py` via the `codes-sync` pre-commit hook (`scripts/check_codes_sync.py --write`). Edits to `errors.py` regenerate the doc table automatically on commit; CI verifies the two stay in lockstep.

See also: ADR-066 (`docs/architecture/adr/066-unified-worker-error-envelope-nats-reply-contracts.mdx`).
