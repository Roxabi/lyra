# Quickstart

Get Lyra running and send your first message in about 5 minutes.

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.12+ | `python --version` |
| [uv](https://docs.astral.sh/uv/) | latest | `pip install uv` |
| [Claude Code CLI](https://claude.ai/download) | latest | Default LLM backend — requires a Claude subscription |
| Telegram bot token | — | Create one via [@BotFather](https://t.me/BotFather) |
| Discord bot token | — | Create one via [Discord Developer Portal](https://discord.com/developers) |

> You only need the channels you plan to use. Skip Telegram or Discord vars if you're not using that channel — the adapter will fail to start, but the other one will still run.

## 1. Install

```bash
git clone https://github.com/roxabi/lyra
cd lyra
uv sync

# Activate the virtual environment to get the `lyra` CLI on your PATH
source .venv/bin/activate
# Alternative: add .venv/bin to your PATH permanently in ~/.bashrc
```

## 2. Configure environment

Create a `.env` file at the project root:

```bash
# Telegram (required if using Telegram adapter)
TELEGRAM_TOKEN=123456789:ABCdef...        # from BotFather → /newbot
TELEGRAM_WEBHOOK_SECRET=any-random-string  # used to verify webhook calls; polling mode ignores this
TELEGRAM_BOT_USERNAME=your_bot_username    # optional, defaults to "lyra_bot"

# Discord (required if using Discord adapter)
DISCORD_TOKEN=MTIz...                      # from Discord Developer Portal → Bot → Token
```

### Telegram: create a bot

1. Open Telegram, search `@BotFather`
2. Send `/newbot` and follow the prompts
3. Copy the token (format: `123456789:ABCdef...`) into `TELEGRAM_TOKEN`
4. Set `TELEGRAM_BOT_USERNAME` to the username you chose (without `@`)

### Discord: create a bot

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications) → New Application
2. Bot tab → Reset Token → copy into `DISCORD_TOKEN`
3. Bot tab → enable **Message Content Intent** (required to read messages)
4. OAuth2 → URL Generator → scope `bot` + permission `Send Messages` → invite the bot to your server

## 3. Configure the agent (optional)

Agents are managed via **AgentStore** (SQLite at `~/.lyra/config.db`). TOML files in `src/lyra/agents/` (system defaults) and `~/.lyra/agents/` (user overrides) are seed sources — import them into the DB on first setup:

```bash
# First-time: seed DB from TOML files
lyra agent init

# List all agents in DB
lyra agent list

# Edit an agent interactively (changes take effect on restart)
lyra agent edit lyra_default

# Validate an agent
lyra agent validate lyra_default
```

Agent seeds are TOML files — no Python needed:

```toml
[agent]
name = "lyra_default"
memory_namespace = "lyra"
permissions = []

[model]
backend = "claude-cli"          # "claude-cli" | "anthropic-sdk"
model = "claude-sonnet-4-6"
max_turns = 10
tools = ["Read", "Grep", "Glob", "WebFetch", "WebSearch"]

[prompt]
system = """You are Lyra, a personal AI assistant..."""
```

**User-level overrides**: put your customised TOML at `~/.lyra/agents/<name>.toml` — it takes precedence over the system default at `init` time.

**Second agent**: duplicate any `.toml` under a new name, run `lyra agent init`, then add a `[[telegram.bots]]` or `[[discord.bots]]` entry in `config.toml` pointing `agent = "<name>"`. No Python changes needed.

## 4. Run

```bash
lyra start
```

Expected output:

```
2026-01-01 12:00:00 INFO lyra.__main__: Lyra started — Telegram + Discord adapters running.
```

Lyra is now:
- Polling Telegram for new messages (aiogram long-polling)
- Connected to Discord via gateway WebSocket

Send a message to your Telegram bot or Discord bot — you should get a reply within a few seconds.

### Telegram-only or Discord-only

Just configure only the platforms you need in `config.toml`. A platform with no `[[telegram.bots]]` / `[[discord.bots]]` entries is silently skipped:

```toml
# Telegram only — omit [[discord.bots]] entirely
[[telegram.bots]]
bot_id = "lyra"
token = "env:TELEGRAM_TOKEN"
bot_username = "env:TELEGRAM_BOT_USERNAME"
webhook_secret = "env:TELEGRAM_WEBHOOK_SECRET"
agent = "lyra_default"

[[auth.telegram_bots]]
bot_id = "lyra"
default = "blocked"
owner_users = [YOUR_TELEGRAM_ID]
```

No flags needed — presence in `config.toml` is the switch.

## 5. Run tests

```bash
uv run pytest
```

All tests are in `tests/` and run with `pytest-asyncio`. No external services required — adapters are mocked.

## 6. Lint and typecheck

```bash
uv run ruff check .      # lint
uv run ruff format .     # format
uv run pyright           # type check
```

## Troubleshooting

**`Missing required env var: TELEGRAM_TOKEN`**
The `.env` file was not found or the variable is empty. Make sure `.env` is in the project root and has no leading/trailing spaces around the `=`.

**Discord bot doesn't respond**
Check that **Message Content Intent** is enabled in the Discord Developer Portal under Bot settings. Without it, the bot receives events but cannot read message content.

**Claude CLI errors**
The default agent uses `claude-cli` backend, which shells out to the `claude` CLI. Make sure you're logged in: run `claude` once to authenticate. If you don't have a Claude subscription, the Anthropic SDK driver (#76) is available — set `backend = "anthropic-sdk"` and provide `ANTHROPIC_API_KEY` in `.env`. Ollama (`backend = "ollama"`) is Phase 2 and not yet available.

**Queue full warning**
If the hub logs `Processing your request…`, the bounded queue (100) is full. This is expected under burst load — messages are queued and processed in order.

## Running Multiple Bots

Lyra supports running multiple bots (each with its own persona and model) — all sharing the hub and adapter processes. The short version:

1. Create an agent TOML in `src/lyra/agents/<name>.toml` for the new persona. Copy `lyra_default.toml` and edit `[agent].name`, `[model].model`, and `[prompt]`. Then run `lyra agent init` to import it into the DB.
2. Add `[[telegram.bots]]` and/or `[[discord.bots]]` entries to `config.toml`, each with a unique `bot_id` and `agent = "<name>"`.
3. Add matching `[[auth.telegram_bots]]` / `[[auth.discord_bots]]` entries and the new bot tokens to `.env`.

See [MULTI-BOT.md](MULTI-BOT.md) for the full configuration reference, auth options, Discord thread ownership details, and a complete step-by-step checklist.

## Next steps

- [Architecture](ARCHITECTURE.md) — understand the hub, bindings, pools, and memory model
- [Vision](vision.md) — design principles and what Lyra is not
- [ADRs](architecture/adr/) — key decisions and their rationale
