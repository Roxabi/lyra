# Multi-Bot Support

Run multiple bots in a single Lyra process — each with its own persona, model, and auth policy — without duplicating infrastructure.

## What multi-bot support enables

A single Lyra instance can host any number of bots across Telegram and Discord simultaneously. Each bot:

- Has a dedicated agent (persona, system prompt, model, memory namespace)
- Has its own auth configuration (who can talk to it)
- Shares one OS process, one event loop, and one `CliPool`
- Routes every conversation to an isolated Pool — no cross-bot history leakage

Typical use case: `lyra_default` (Claude Sonnet, developer-facing) and `aryl_default` (Claude Haiku, lighter tasks) running side by side on the same server.

---

## config.toml: single-bot vs multi-bot

### Single-bot (legacy, still works)

```toml
[auth.telegram]
default = "blocked"
owner_users = [7377831990]

[telegram]
token = "env:TELEGRAM_TOKEN"
bot_username = "env:TELEGRAM_BOT_USERNAME"
webhook_secret = "env:TELEGRAM_WEBHOOK_SECRET"
agent = "lyra_default"

[auth.discord]
default = "blocked"
owner_users = [389408866774810625]

[discord]
token = "env:DISCORD_TOKEN"
auto_thread = true
agent = "lyra_default"
```

The flat `[telegram]` and `[discord]` sections use `bot_id = "main"` internally.

### Multi-bot

Replace the flat sections with `[[telegram.bots]]` and `[[discord.bots]]` arrays. Each entry takes a `bot_id` that must match a corresponding `[[auth.telegram_bots]]` or `[[auth.discord_bots]]` entry.

```toml
[admin]
user_ids = ["tg:user:7377831990", "dc:user:389408866774810625"]

[[telegram.bots]]
bot_id = "lyra"
token = "env:TELEGRAM_TOKEN"
bot_username = "env:TELEGRAM_BOT_USERNAME"
webhook_secret = "env:TELEGRAM_WEBHOOK_SECRET"
agent = "lyra_default"

[[telegram.bots]]
bot_id = "aryl"
token = "env:ARYL_TELEGRAM_TOKEN"
bot_username = "RoxabiArylbot"
agent = "aryl_default"
# webhook_secret = "env:ARYL_TELEGRAM_WEBHOOK_SECRET"  # required for webhook mode

[[discord.bots]]
bot_id = "lyra"
token = "env:DISCORD_TOKEN"
auto_thread = true
agent = "lyra_default"

[[discord.bots]]
bot_id = "aryl"
token = "env:ARYL_DISCORD_TOKEN"
auto_thread = true
agent = "aryl_default"

[[auth.telegram_bots]]
bot_id = "lyra"
default = "blocked"
owner_users = [7377831990]

[[auth.telegram_bots]]
bot_id = "aryl"
default = "blocked"
owner_users = [7377831990]

[[auth.discord_bots]]
bot_id = "lyra"
default = "blocked"
owner_users = [389408866774810625]

[[auth.discord_bots]]
bot_id = "aryl"
default = "blocked"
owner_users = [389408866774810625]
```

Every `bot_id` string is arbitrary but must be unique per platform and consistent across the `bots` and `auth_bots` arrays.

---

## Agent TOML: defining a persona

Each bot references an agent by name (`agent = "aryl_default"`). Agent configs live at `src/lyra/agents/<name>.toml`.

```toml
[agent]
name = "aryl_default"
memory_namespace = "aryl"
show_intermediate = false

[model]
backend = "claude-cli"
model = "claude-haiku-4-5-20251001"
max_turns = 20
cwd = "~/projects/lyra"

# [prompt]
# system = "..."  # Optional: raw string overrides persona composition

[workspaces]
lyra     = "~/projects/lyra"
projects = "~/projects"
```

Key fields:

| Field | Purpose |
|-------|---------|
| `name` | Must match the filename (without `.toml`) |
| `memory_namespace` | Isolates SQLite memory — different bots never share memories |
| `backend` | `claude-cli` or `anthropic-sdk` |
| `model` | Model identifier passed to the backend |
| `model.cwd` | Working directory for the Claude subprocess |
| `[workspaces]` | Named directory shortcuts exposed as `/keyname` slash commands |

Multiple bots can share an agent file (same persona, same memory namespace). Distinct bots typically use distinct agents.

### smart_routing constraint

`smart_routing` (complexity-based model selection) only works with `backend = "anthropic-sdk"`. If the agent uses `backend = "claude-cli"`, smart_routing must be disabled or absent from the config. `aryl_default` uses `claude-cli`, so it does not enable smart_routing.

---

## Per-bot auth

Each bot's auth block controls who can send messages to it. The `bot_id` field links it to the corresponding `[[telegram.bots]]` or `[[discord.bots]]` entry.

```toml
[[auth.telegram_bots]]
bot_id = "aryl"
default = "blocked"          # block everyone by default
owner_users = [7377831990]   # Telegram user IDs with owner trust
```

Trust levels:

| Level | Meaning |
|-------|---------|
| `owner` | Full access, admin commands |
| `trusted` | Standard user access |
| `public` | Allowed through the auth gate (not blocked), but no special privileges beyond access. Note: in Phase 1 the only enforcement is BLOCKED vs non-BLOCKED — `public` and `trusted` are treated identically by the adapters. Distinct skill-level gating based on trust is not yet implemented. |
| `blocked` | No access — messages are silently dropped |

`default = "blocked"` is recommended for personal bots. All other users are silently ignored.

---

## Discord: thread ownership and cross-bot silence

When multiple Discord bots share a server, each bot must only respond in channels and threads it owns — otherwise both bots respond to every message.

Lyra enforces this via per-adapter `_owned_threads`:

- When `auto_thread = true`, a bot creates a new thread for each conversation it starts. That thread's ID is added to its `_owned_threads` set.
- When a bot is directly mentioned inside an existing thread (one it did not create), that thread is also added to `_owned_threads`. This is the second ownership path — claiming via first mention.
- When a message arrives in a thread, the adapter checks whether the thread is in `_owned_threads`. If not, and the bot was not directly mentioned, the message is silently dropped.
- Direct mentions (`@botname`) always bypass the ownership check.
- `_owned_threads` is not persisted across restarts — ownership is re-established on the first mention after restart.

This means two bots can coexist in the same Discord server without interfering: each responds only in threads it created or where it was explicitly mentioned.

`auto_thread` behavior:

| Setting | Effect |
|---------|--------|
| `auto_thread = true` | Bot creates a thread for the first reply in a channel message, then continues in that thread |
| `auto_thread = false` | Bot replies inline in the channel |

For multi-bot setups, `auto_thread = true` is strongly recommended — it makes ownership unambiguous.

---

## Telegram: per-bot webhook routing

Each Telegram bot registers a distinct webhook URL:

```
/webhooks/telegram/{bot_id}
```

When Telegram delivers an update, the `bot_id` path parameter identifies which bot and which agent should handle the message. No additional routing logic is needed — the URL carries the identity.

In polling mode (the only mode currently supported), aiogram routes updates to the correct bot instance automatically by token.

---

## Routing and conversation isolation

The routing key is a 3-tuple: `RoutingKey(platform, bot_id, scope_id)`.

- `platform` — `"telegram"` or `"discord"`
- `bot_id` — the string from `config.toml` (`"lyra"`, `"aryl"`, etc.)
- `scope_id` — the conversation scope extracted by the adapter:
  - Telegram DM / group: `chat:{chat_id}`
  - Telegram forum topic: `chat:{chat_id}:topic:{topic_id}`
  - Discord thread: `thread:{thread_id}`
  - Discord channel: `channel:{channel_id}`

Every unique `(platform, bot_id, scope_id)` combination gets its own Pool. Pools are independent: separate conversation history, separate session state, separate asyncio task. Two bots talking to the same user in the same channel each have their own Pool.

The OutboundDispatcher validates the `(platform, bot_id)` pair before sending any response, preventing a response from leaking to the wrong bot's channel.

---

## Per-agent resources vs shared resources

| Resource | Per-agent | Shared |
|----------|-----------|--------|
| ProviderRegistry | Yes | No |
| SmartRoutingDecorator | Yes | No |
| Memory namespace (SQLite) | Yes | No |
| System prompt / persona | Yes | No |
| CliPool (subprocess pool) | No | Yes (all agents) |
| asyncio event loop | No | Yes |
| Inbound / outbound bus | No | Yes |

The `CliPool` is the Claude CLI subprocess pool. It is shared across all agents to cap the number of live subprocesses regardless of how many bots are running.

---

## Adding a new bot: step-by-step checklist

1. **Create the bot on the platform**
   - Telegram: use `@BotFather` → `/newbot` → copy the token
   - Discord: Discord Developer Portal → New Application → Bot → Reset Token → enable Message Content Intent

2. **Create the agent TOML** (if using a new persona)
   - Copy `src/lyra/agents/lyra_default.toml` to `src/lyra/agents/<name>.toml`
   - Edit `name`, `memory_namespace`, `model`, and `[prompt]`
   - If using `backend = "claude-cli"`, do not enable `smart_routing`

3. **Add environment variables** to `.env`
   ```bash
   ARYL_TELEGRAM_TOKEN=123456789:ABCdef...
   ARYL_DISCORD_TOKEN=MTIz...
   ```

4. **Add a `[[telegram.bots]]` entry** in `config.toml`
   ```toml
   [[telegram.bots]]
   bot_id = "aryl"
   token = "env:ARYL_TELEGRAM_TOKEN"
   bot_username = "RoxabiArylbot"
   agent = "aryl_default"
   ```

5. **Add a `[[discord.bots]]` entry** in `config.toml`
   ```toml
   [[discord.bots]]
   bot_id = "aryl"
   token = "env:ARYL_DISCORD_TOKEN"
   auto_thread = true
   agent = "aryl_default"
   ```

6. **Add auth entries** in `config.toml` — one per platform
   ```toml
   [[auth.telegram_bots]]
   bot_id = "aryl"
   default = "blocked"
   owner_users = [7377831990]

   [[auth.discord_bots]]
   bot_id = "aryl"
   default = "blocked"
   owner_users = [389408866774810625]
   ```

7. **Verify the bot_id is consistent** — the `bot_id` string must match exactly across the `bots` entry, the `auth_bots` entry, and will appear in logs and webhook URLs.

8. **Restart Lyra**
   ```bash
   make lyra reload
   ```

9. **Test** — send a message to the new bot on each platform. Check `make lyra logs` for the routing key (`platform=telegram bot_id=aryl scope_id=chat:...`).

---

## Constraints summary

| Constraint | Detail |
|------------|--------|
| `smart_routing` requires `anthropic-sdk` | Does not work with `claude-cli` backend |
| `bot_id` must be unique per platform | Two Telegram bots cannot share the same `bot_id` |
| `auth_bots` entry required per bot | A bot without a matching auth entry is silently skipped at startup — add `[[auth.telegram_bots]]` / `[[auth.discord_bots]]` entry or the bot will not start. The process exits only if ALL bots lack auth. |
| `CliPool` is shared | All bots share the subprocess pool — heavy concurrent use increases subprocess contention |
| Single process | All bots run in one Python process — a crash affects all bots |
| TTS not available in multi-bot mode | The `_bootstrap_multibot` path does not initialize `TTSService`. Voice features require the legacy single-bot path. |

---

## Troubleshooting

**Bot does not respond**
Check the logs for registration and ready messages:
```bash
make lyra logs
# Telegram: INFO lyra.__main__: Registered Telegram bot bot_id='<name>' agent='<agent>'
# Discord:  INFO lyra.adapters.discord: Discord bot ready: <BotUsername> (id=<id>)
```
If the line is missing, the bot was skipped at startup — see below.

**Bot silently absent from startup**
Check that `[[auth.telegram_bots]]` / `[[auth.discord_bots]]` entry exists in `config.toml` with a `bot_id` that exactly matches the corresponding `[[telegram.bots]]` / `[[discord.bots]]` entry. A bot with no matching auth entry is silently skipped.

**Wrong bot responding**
Check that each bot's `[[telegram.bots]]` / `[[discord.bots]]` `bot_id` values are unique per platform. Two bots with the same `bot_id` will conflict.
