# Lyra

**Personal AI agent engine** — hub-and-spoke, asyncio, multi-channel.

[![CI](https://github.com/Roxabi/lyra/actions/workflows/ci.yml/badge.svg)](https://github.com/Roxabi/lyra/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white)
![uv](https://img.shields.io/badge/uv-package%20manager-DE5FE9)
![asyncio](https://img.shields.io/badge/concurrency-asyncio-0ea5e9)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

Lyra runs 24/7 on your own hardware, connects Telegram and Discord to specialized AI agents, and routes every conversation through isolated per-scope pools. No cloud lock-in. No subscription. Your data stays on your machines.

## Why

Most personal AI assistants are cloud-hosted: your data leaves your machine, your conversations are stored on someone else's servers, and the service disappears the moment a company pivots.

Lyra exists to run on your own hardware — a Raspberry Pi, a home server, anything always-on — and connect your preferred chat platforms (Telegram, Discord) to AI agents you control. No API keys sold to third parties. No subscription. No lock-in. When you want a different model, you swap it in TOML.

It's for developers who want a persistent personal AI without giving up ownership of their data or infrastructure.

## How it works

1. **Channel adapters** (Telegram, Discord) run as separate processes. They normalize incoming messages and publish them over NATS (`lyra.inbound.<platform>.<bot_id>`).
2. **The Hub** (`lyra-hub` process) subscribes to NATS, routes each message to the right agent via typed `(platform, bot_id, scope_id)` bindings — one pool per conversation scope (chat, thread, channel).
3. **The Agent** processes the message, calls the LLM, and publishes the response over NATS (`lyra.outbound.<platform>.<bot_id>`). The adapter's `NatsOutboundListener` delivers it to the platform.

## Architecture

```
lyra-telegram          lyra-hub                lyra-discord
    │                     │                        │
    │  inbound.telegram   │      clipool.cmd       │  inbound.discord
    ├────────────────────►├───────────────────────►│
    │                     │                        │
    │                     │   ┌────────────┐       │
    │                     │   │  CliPool   │       │
    │                     │   │ (Claude)   │       │
    │                     │   └────────────┘       │
    │                     │                        │
    │  outbound.telegram  │  outbound.discord      │
    ◄─────────────────────┴────────────────────────┘
              NATS message bus
```

**Production**: Four independent processes (`lyra-hub`, `lyra-telegram`, `lyra-discord`, `lyra-clipool`) communicate via NATS. Each runs in its own container.

**Development**: `lyra start` runs everything in one process with an embedded NATS server.

## Features

### Channels & Routing

| Feature | Detail |
|---------|--------|
| **Channels** | Telegram (aiogram v3 · polling + webhook) · Discord (discord.py v2 · gateway) |
| **Routing** | Typed `RoutingKey(platform, bot_id, scope_id)` — scope = chat / thread / channel |
| **Multi-bot** | Multiple bots per platform, each with its own agent binding |
| **Concurrency** | Sequential per scope (`asyncio.Task`) · parallel across scopes |
| **Backpressure** | Bounded queue (100) → immediate ack + blocking await |

### AI & Memory

| Feature | Detail |
|---------|--------|
| **LLM** | Claude CLI subprocess (primary) · smart routing (complexity-based model selection) |
| **Agents** | Stateless singleton · isolated per-scope pools · AgentStore (SQLite) |
| **Memory** | 5 levels: working (L0 compaction) → session → episodic → semantic (FTS5 + embeddings) → procedural |
| **Session commands** | `/vault-add`, `/explain`, `/summarize`, `/search` — scrape → LLM → vault |

### Voice & Security

| Feature | Detail |
|---------|--------|
| **Voice** | STT via voicecli (faster-whisper) · TTS via voicecli (Qwen) · OGG/Opus · Discord voice |
| **Auth** | TrustLevel per adapter (owner/trusted/public/blocked) · outbound verification |
| **Security** | Prompt injection guard · sandboxed skills · hmac webhook verification |

## Quick start

```bash
# 1. Install
uv sync && source .venv/bin/activate

# 2. Configure
cp config.toml.example config.toml
# Edit config.toml: set bot_id, agent, owner_users

# 3. Store credentials (encrypted)
lyra bot add --platform telegram --bot-id <bot_id>

# 4. Initialize agents
lyra agent init

# 5. Run
lyra start
```

See [QUICKSTART.md](docs/QUICKSTART.md) for full setup — bot creation, environment variables, first message.

## CLI reference

### Server

```bash
lyra                        # start unified (hub + adapters, embedded NATS)
lyra start                  # same as above
lyra hub                    # standalone hub (requires external NATS)
lyra adapter telegram       # standalone Telegram adapter
lyra adapter discord        # standalone Discord adapter
lyra adapter clipool        # standalone CliPool worker
```

### Agent management

```bash
lyra agent init             # seed DB from TOML files
lyra agent init --force     # overwrite existing
lyra agent list             # list all agents
lyra agent show <name>      # full config for one agent
lyra agent edit <name>      # interactive edit
lyra agent validate <name>  # validate schema
lyra agent create           # create new agent (writes TOML)
```

### Bot credentials

```bash
lyra bot add --platform telegram --bot-id <id>   # store encrypted token
lyra bot add --platform discord --bot-id <id>
lyra bot list                                    # list stored (masked)
lyra bot remove --platform telegram --bot-id <id>
```

### Configuration

```bash
lyra config show            # display parsed config.toml
lyra config validate        # validate config + env vars
```

### Setup & ops

```bash
lyra setup commands         # register /commands with Telegram
lyra ops verify             # verify NATS ACL permissions
lyra voice-smoke            # test STT/TTS pipeline
lyra --version              # print version
```

## In-chat commands

| Command | Description |
|---------|-------------|
| `/vault-add <url>` | Scrape URL → LLM summary → save to vault |
| `/explain <url>` | Scrape URL → plain-language explanation |
| `/summarize <url>` | Scrape URL → bullet-point summary |
| `/search <query>` | Full-text search over vault |
| `<url>` (bare) | Auto-rewritten to `/vault-add <url>` |
| `/clear` / `/new` | Reset conversation history |
| `/stop` | Cancel current processing |
| `/voice <text>` | Voice reply via TTS |
| `/workspace <name>` | Switch working directory |
| `/circuit` | Circuit breaker status (admin) |
| `/config` | Runtime config (admin) |
| `/help` | List commands |

## Configuration

Two files:

- `config.toml` — bot instances, auth rules, adapter settings
- `.env` — secrets: `NATS_URL`, `ANTHROPIC_API_KEY` (optional for CLI driver)

Agent configs stored in `~/.lyra/config.db` (SQLite). TOML files in `src/lyra/agents/` are seed sources — import with `lyra agent init`.

## Project structure

```
src/lyra/
  core/        — hub, pool, agent, memory, auth, commands
  adapters/    — Telegram, Discord, CLI
  nats/        — NatsBus, NatsChannelProxy
  bootstrap/   — process entry points
  agents/      — SimpleAgent implementation
  llm/         — LlmProvider protocol, Claude CLI driver
  stt/         — STT service (faster-whisper via NATS)
  tts/         — TTS pipeline (voicecli)
  commands/    — plugin commands (/vault-add, /search)
  monitoring/  — health checks, escalation
  agent_cmd/   — agent CLI commands
packages/
  roxabi-nats/     — NATS transport SDK
  roxabi-contracts/ — NATS contract schemas
tests/        — pytest-asyncio
docs/         — ARCHITECTURE, ADRs, guides
```

## Documentation

| Doc | Description |
|-----|-------------|
| [QUICKSTART.md](docs/QUICKSTART.md) | Zero to first message |
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | Hub design, memory model, decisions |
| [ROADMAP.md](docs/ROADMAP.md) | Phase 1/2/3 scope |
| [COMMANDS.md](docs/COMMANDS.md) | Command router, plugins |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | Quadlet containers, logs |
| [ADRs](docs/architecture/adr/) | Architecture decision records |

## License

MIT
