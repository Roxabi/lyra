# Lyra — Architecture & Decisions

> Living hub. Routes to domain pages (SSoT) + ADRs (historical why). Updated as decisions are made.

---

## How to read this docs tree

**Start here for current state**: 13 living domain pages, each the single source of truth for its area.

| Domain page | What it owns |
|---|---|
| [messaging.md](architecture/messaging.md) | NATS subjects, routing key, hub dispatch, KV readiness, chunk protocol |
| [llm-streaming.md](architecture/llm-streaming.md) | LlmEvent → StreamProcessor → RenderEvent pipeline, AG-UI v2 |
| [adapters.md](architecture/adapters.md) | Telegram, Discord, CLI inbound, audio routing, TTS overlay |
| [storage.md](architecture/storage.md) | Agent / thread / blob stores, memory scope, event bus DI |
| [security-routing.md](architecture/security-routing.md) | Auth, trust, command parser, memory isolation, NATS infra security |
| [deployment.md](architecture/deployment.md) | C3 container split, Quadlet ecosystem, autodeploy, hardware specs |
| [contracts.md](architecture/contracts.md) | roxabi-nats SDK, roxabi-contracts schemas, voice routing |
| [workers-tooling.md](architecture/workers-tooling.md) | CliPool, processor registry, tool integration, importlinter |
| [architecture-patterns.md](architecture/architecture-patterns.md) | Clean / Hexagonal / Kernel patterns + engineering invariants |
| [testing-conventions.md](architecture/testing-conventions.md) | Test taxonomy, fixture policy, mock boundaries, CI gate conventions |
| [voice-to-voice-analysis.md](architecture/voice-to-voice-analysis.md) | Voice pipeline design, audio latency budgets, STT/TTS adapter contracts |
| [target-architecture.md](architecture/target-architecture.md) | Hexagonal/Ports & Adapters layout as implemented (file paths, module structure) |
| [CURRENT.generated.md](architecture/CURRENT.generated.md) | Machine-generated inventory SSoT — layers, subjects, topology, entry points |

**Decision archive** — 59 active ADRs (26 archived) in [`architecture/adr/`](architecture/adr/) preserve historical reasoning. Each ADR has a redirect banner to its domain page. **Read ADRs only when you need the *why* behind a decision**, not the *what*. Index grouped by domain in [`adr/meta.json`](architecture/adr/meta.json).

**Implementation reference** — [target-architecture.md](architecture/target-architecture.md) shows the Hexagonal/Ports & Adapters layout as implemented (file paths, module structure).

---

## What is Lyra

Hub-and-spoke AI agent engine. One hub routes inbound messages from multiple platforms (Telegram, Discord, CLI) to per-conversation pools backed by a Claude CLI subprocess. Responses stream back through NATS to the originating adapter. All state is per-pool; agents are immutable singletons. Multiple bots per platform are supported via independent bindings.

```
lyra_telegram                     lyra_hub                      lyra_discord
─────────────                   ─────────────                  ─────────────
aiogram long-poll                NatsBus                       discord.py gateway
      │                              │                              │
      │ lyra.inbound.telegram.<bot>  │ lyra.inbound.discord.<bot>   │
      ├─────────────────────────────▶│◄─────────────────────────────┤
      │                              │ InboundBus → Hub → resolve_binding()
      │                              │                              │
      │                              │ get_or_create_pool()         │
      │                              │                              │
      │                    lyra.clipool.cmd ──▶ lyra_clipool process│
      │                              │         │                    │
      │                    lyra.clipool.heartbeat ◄─┘              │
      │                              │                              │
      │ lyra.outbound.telegram.<bot>   │  lyra.outbound.discord.<bot> │
      ◄──────────────────────────────┴──────────────────────────────▶
```

All four processes run on Machine 1 (hub). NATS topics: `lyra.inbound.<platform>.<bot_id>` (adapter→hub), `lyra.outbound.<platform>.<bot_id>` (hub→adapter). `lyra start` runs hub + adapters in one process with embedded NATS.

---

## Key Invariants

1. **Library first** — `lyra` is a Python library; CLI is a thin shell. `uv add --editable path/to/lyra` works.
2. **No side effects on import** — engines load lazily; `import lyra` is instant.
3. **Agent = stateless singleton** — immutable config (prompt, permissions, namespace). All mutable state lives in the Pool.
4. **Adapters send `trust=PUBLIC`** — trust resolution is Hub-side only (C3 pattern). Adapters never decide who may speak.
5. **User-scoped memory** — every memory query must include `user_id`. Even stats, aggregate per user.
6. **Bounded queues** — `asyncio.Queue(maxsize=100)` per channel. Full → immediate ack + blocking `await put()`.
7. **Module ≤300 LOC** — enforced by `check_file_length.sh`. Hub, agent, pool, adapters all decomposed.

---

## Agents, Bots, and Bindings

| Concept | What it is | Where it lives | Managed by |
|---|---|---|---|
| **Bot** | Platform identity (`@RoxabiLyraBot`). Owns token, `bot_id`, `platform`. | `config.toml` → `BotStore` (`config.db` `bots`) | `lyra bot init` |
| **Agent** | AI brain. `model`, `backend`, `persona`, `tools`, `plugins`. | `src/lyra/agents/*.toml` → `AgentStore` (`config.db` `agents`) | `lyra agent init` |
| **Binding** | `bot_id` → `agent_name` for a conversation scope. | `config.db` `bot_agent_map` | `lyra agent assign` / `unassign` |

Routing: `(platform, bot_id, scope_id)` → `(agent, pool_id)`. One pool per scope. Scope = `chat:{id}` | `thread:{id}` | `channel:{id}`.

---

## Current Status

**Phase 1b complete.** All items shipped. Phase 2 (atomic SLMs) deferred until Machine 1 VRAM budget is validated.

See [ROADMAP.md](ROADMAP.md) for backlog and priorities.
