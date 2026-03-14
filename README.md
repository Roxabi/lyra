# Lyra

**Personal AI agent engine** — hub-and-spoke, asyncio, multi-channel.

[![CI](https://github.com/Roxabi/lyra/actions/workflows/ci.yml/badge.svg)](https://github.com/Roxabi/lyra/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)
![uv](https://img.shields.io/badge/uv-package%20manager-DE5FE9)
![version](https://img.shields.io/badge/version-0.1.0-22c55e)
![asyncio](https://img.shields.io/badge/concurrency-asyncio-0ea5e9)

Lyra runs 24/7 on your own hardware, connects Telegram and Discord to specialized AI agents, and routes every conversation through isolated per-scope pools. No cloud lock-in. No subscription. Your data stays on your machines.

## Why

Most personal AI assistants are cloud-hosted: your data leaves your machine, your conversations are stored on someone else's servers, and the service disappears the moment a company pivots.

Lyra exists to run on your own hardware — a Raspberry Pi, a home server, anything always-on — and connect your preferred chat platforms (Telegram, Discord) to AI agents you control. No API keys sold to third parties. No subscription. No lock-in. When you want a different model, you swap it in TOML.

It's for developers who want a persistent personal AI without giving up ownership of their data or infrastructure.

## How it works

1. **Channel adapters** (Telegram, Discord) normalize incoming messages and push them into per-platform bounded asyncio queues.
2. **The Hub** routes each message to the right agent via typed `(platform, bot_id, scope_id)` bindings — one pool per conversation scope (chat, thread, channel).
3. **The Agent** processes the message, calls the LLM, and sends the response back through the outbound dispatcher to the originating channel.

## Architecture

```mermaid
flowchart TD
    TG["Telegram<br/>aiogram v3 · polling"] --> TQ["tg_inbound<br/>Queue"]
    DC["Discord<br/>discord.py v2 · gateway"] --> DQ["dc_inbound<br/>Queue"]
    TQ --> STG["InboundBus<br/>staging Queue"]
    DQ --> STG
    STG --> HUB["Hub<br/>resolve_binding()"]
    HUB -->|"telegram · main · chat:555"| P1["Pool<br/>asyncio.Task"]
    HUB -->|"discord · main · thread:888"| P2["Pool<br/>asyncio.Task"]
    P1 --> AGENT["Agent<br/>stateless singleton"]
    P2 --> AGENT
    AGENT --> LLM["LLM<br/>LlmProvider<br/>Claude CLI + SDK"]
    P1 --> TGO["tg_outbound<br/>OutboundDispatcher"]
    P2 --> DCO["dc_outbound<br/>OutboundDispatcher"]
    TGO --> TG
    DCO --> DC
```

## Features

### Channels & Routing

| Feature | Detail |
|---------|--------|
| **Channels** | Telegram (aiogram v3 · polling + webhook) · Discord (discord.py v2 · gateway) |
| **Routing** | Typed `RoutingKey(platform, bot_id, scope_id)` · wildcard `*` per channel · scope = chat / thread / channel |
| **Concurrency** | Sequential per scope (`asyncio.Task`) · parallel across scopes and platforms — zero config |
| **Backpressure** | Bounded queue (100) → immediate ack + blocking `await put()` |

### AI & Agents

| Feature | Detail |
|---------|--------|
| **LLM** | LlmProvider protocol: Claude CLI + Anthropic SDK drivers · smart routing (complexity-based model selection) · Ollama (Phase 2) |
| **Agents** | Stateless singleton · isolated per-scope pools · TOML config per agent · N agents × N bots via `config.toml` |
| **Memory** | 5 levels: working (L0 compaction ✅) → session → episodic → semantic (SQLite + FTS5 + fastembed ✅) → procedural · cross-session recall via `[MEMORY]`/`[PREFERENCES]` blocks |

### Security & Voice

| Feature | Detail |
|---------|--------|
| **Auth** | AuthMiddleware + TrustLevel per adapter (owner/trusted/public/blocked) · RoutingContext outbound verification |
| **Voice** | STT via voicecli library (faster-whisper `large-v3-turbo` + personal vocab · InboundAudioBus → STTService) · TTS via voicecli (Phase 2) |
| **Security** | Prompt injection guard · sandboxed skills · least-privilege tool permissions · hmac webhook verification |

## Quick start

```bash
# 1. Install
uv sync

# 2. Configure
cat > .env <<'EOF'
TELEGRAM_TOKEN=your-telegram-bot-token
TELEGRAM_WEBHOOK_SECRET=any-random-secret
DISCORD_TOKEN=your-discord-bot-token
EOF

# 3. Run
lyra start

# Or see all available commands
lyra --help
```

> See [QUICKSTART.md](docs/QUICKSTART.md) for the full setup — bot creation, agent TOML, environment variables, and sending your first message.

## Configuration

All configuration is via `.env` (copy `.env.example` to get started). Key variables:

**Telegram**

| Variable | Required | Description |
|----------|----------|-------------|
| `TELEGRAM_TOKEN` | ✅ | Bot token from [@BotFather](https://t.me/BotFather) |
| `TELEGRAM_BOT_USERNAME` | optional | Bot username (e.g. `lyra_bot`) — defaults to `lyra_bot` |
| `TELEGRAM_WEBHOOK_SECRET` | ✅ | Any random string — used to verify webhook payloads |
| `TELEGRAM_ADMIN_CHAT_ID` | optional | Chat ID that receives owner-level trust |

**Discord**

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_TOKEN` | ✅ | Bot token from Discord Developer Portal |
| `DISCORD_AUTO_THREAD` | optional | Auto-create threads for replies (`true`/`false`) |

**LLM & Config**

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | ✅ (SDK driver) | Anthropic API key (not needed for Claude CLI driver) |
| `LYRA_CONFIG` | optional | Path to `config.toml` — default: `config.toml` |

**Voice (optional)**

| Variable | Default | Description |
|----------|---------|-------------|
| `STT_MODEL_SIZE` | `large-v3-turbo` | Whisper model size (`small`, `medium`, `large-v3-turbo`) |
| `STT_DEVICE` | `auto` | `cpu`, `cuda`, or `auto` |

Agent behaviour (tools, model, system prompt) is configured per-agent in `src/lyra/agents/<name>.toml` (project-level) or `~/.lyra/agents/<name>.toml` (user-level, takes precedence).
See [QUICKSTART.md](docs/QUICKSTART.md) for the full walkthrough.

## Operations (Makefile)

Machine connection is configured in `.env` (see `.env.example`):

```bash
MACHINE1_HOST=mickael@192.168.1.16
MACHINE1_DIR=~/projects/lyra
```

| Command | Description |
|---------|-------------|
| `make lyra` | Start Lyra locally (via supervisor) |
| `make lyra stop` | Stop Lyra locally |
| `make lyra status` | Check local service status |
| `make lyra logs` | Tail local stdout logs |
| `make deploy` | Deploy to Machine 1 (pull main + test + restart) |
| `make remote stop` | Stop Lyra on Machine 1 |
| `make remote status` | Check Machine 1 service status |
| `make remote logs` | Tail Machine 1 stdout logs |
| `make test` | Run tests |
| `make lint` | Run ruff linter |
| `make format` | Auto-format with ruff |

## Structure

```
src/lyra/
  core/
    message.py             — InboundMessage, OutboundMessage, RoutingKey, Platform, Response
    hub.py                 — Hub (bus + adapter registry + bindings + TTL eviction)
    pool.py                — Pool (history + session identity fields + asyncio.Task per scope)
    agent.py               — AgentBase (compact, flush_session, build_system_prompt), Agent config
    memory.py              — MemoryManager (recall, upsert_session, concept/preference extraction)
    auth.py                — AuthMiddleware (per-adapter trust verification)
    trust.py               — TrustLevel enum (owner/trusted/public/blocked)
    cli_pool.py            — Claude CLI subprocess pool
    inbound_bus.py         — InboundBus (per-platform queues + staging)
    inbound_audio_bus.py   — InboundAudioBus (per-platform bounded audio queues)
    outbound_dispatcher.py — OutboundDispatcher (per-platform outbound + CB + audio/attachment)
    command_router.py      — CommandRouter (builtins + plugin commands)
    circuit_breaker.py     — CircuitBreaker + CircuitRegistry
    pairing.py             — PairingManager (cross-platform identity linking)
    plugin_loader.py       — PluginLoader (TOML manifest + dynamic import)
    debouncer.py           — Debouncer (typing-aware message aggregation)
    runtime_config.py      — RuntimeConfig (mutable agent overlay via !config)
    messages.py            — MessageManager (i18n-ready message templates)
  adapters/
    _shared.py      — shared adapter helpers (normalization, render functions)
    telegram.py     — aiogram v3 adapter (polling + webhook)
    discord.py      — discord.py v2 gateway adapter
    cli.py          — CLI adapter (stdin/stdout)
  agents/
    simple_agent.py       — Claude CLI agent implementation
    anthropic_agent.py    — Anthropic SDK agent implementation
    lyra_default.toml     — default agent config (model, tools, system prompt)
  llm/
    base.py               — LlmProvider protocol
    drivers/cli.py        — ClaudeCliDriver
    drivers/sdk.py        — AnthropicSdkDriver
    registry.py           — driver registry
    smart_routing.py      — complexity-based model selection
    decorators.py         — LLM call decorators
  stt/                    — STTService + STTConfig (faster-whisper)
  plugins/                — Plugin handlers (echo, pairing)
  monitoring/             — Health checks (two-tier /health) + escalation
  errors.py               — ProviderError + shared error types
tests/
  core/             — unit + integration tests (pytest-asyncio, pytest-cov)
  adapters/         — adapter tests (streaming, voice, normalization)
docs/
  ARCHITECTURE.md   — full technical spec and decisions
  ROADMAP.md        — priorities and scope
  QUICKSTART.md     — developer setup guide
  vision.md         — design principles and constraints
  architecture/adr/ — architecture decision records (18 ADRs)
```

## Documentation

| Doc | Description |
|-----|-------------|
| [QUICKSTART.md](docs/QUICKSTART.md) | From zero to first message in 5 minutes |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Hub design, memory model, hardware, key decisions |
| [vision.md](docs/vision.md) | Why Lyra exists and what it is not |
| [ROADMAP.md](docs/ROADMAP.md) | Phase 1/2/3 scope, priorities, timeline |
| [COMMANDS.md](docs/COMMANDS.md) | Command router — slash commands, external tool integration pattern |
| [GETTING-STARTED.md](docs/GETTING-STARTED.md) | Machine 1 (Ubuntu Server) hardware setup |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | Production service management on Machine 1 (supervisord, logs, firewall) |
| [ADRs](docs/architecture/adr/) | 18 architecture decision records with full rationale |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Branching model, commit conventions, adding adapters and agents |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) — branching model, commit conventions, adding adapters and agents.

## License

MIT
