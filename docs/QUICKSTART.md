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

The default agent config lives at `src/lyra/agents/lyra_default.toml`. You can edit it without touching any Python:

```toml
[agent]
name = "lyra_default"
memory_namespace = "lyra"
permissions = []

[model]
backend = "claude-cli"          # "claude-cli" (default) | "anthropic-sdk" | "ollama" (Phase 2)
model = "claude-haiku-4-5-20251001"
max_turns = 10
tools = ["Read", "Grep", "Glob", "WebFetch", "WebSearch"]

[prompt]
system = """You are Lyra, a personal AI assistant..."""
```

To create a second agent, duplicate the file (`my_agent.toml`) and pass its name to `load_agent_config()` in `__main__.py`.

## 4. Run

```bash
python -m lyra
```

Expected output:

```
2026-01-01 12:00:00 INFO lyra.__main__: Lyra started — Telegram + Discord adapters running.
```

Lyra is now:
- Polling Telegram for new messages (aiogram long-polling)
- Connected to Discord via gateway WebSocket

Send a message to your Telegram bot or Discord bot — you should get a reply within a few seconds.

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

## Next steps

- [Architecture](ARCHITECTURE.md) — understand the hub, bindings, pools, and memory model
- [Vision](vision.md) — design principles and what Lyra is not
- [ADRs](architecture/adr/) — key decisions and their rationale
