# Lyra — Architecture & Decisions

> Living document. Updated as decisions are made.
> Last updated: 2026-03-17 (Phase 1b complete + architecture refactoring: module decomposition #294–#312, auth split #313/#314, deduplication, timeout hardening #317, session resumption #318)

---

## Python-first Paradigm

> Adopted 2026-03-08. All new Roxabi projects follow this model.

**No more monolithic servers.** Every project ships as:

1. **A Python library** — importable package with a clean public API (`__init__.py` with `__all__`). Other projects depend on it directly via `uv` path/git dependency.
2. **A CLI entrypoint** — thin shell over the library (`cli.py`), installed via `[project.scripts]`. The CLI adds zero logic; it parses args and calls library functions.

### What "library" means per project

| Project | Package | Public API surface | CLI entrypoint |
|---------|---------|-------------------|---------------|
| `voiceCLI` | `voicecli` | `generate`, `generate_async`, `clone`, `clone_async`, `transcribe`, `transcribe_async`, `list_engines`, `list_voices` | `voicecli` |
| `imageCLI` | `imagecli` | `generate`, `get_engine`, `list_engines`, `preflight_check`, `load_config`, `parse_prompt_file` | `imagecli` |
| `lyra` | `lyra` | Hub, Agent, Pool, InboundMessage, OutboundMessage, ChannelAdapter (internal SDK — not public) | daemon via supervisord |
| `2ndBrain` | `knowledge` | Vault read/write/search (internal SDK) | `knowledge_bot` daemon |

### Rules

- **Library first**: implement in library, expose in CLI. Never implement logic in `cli.py`.
- **No side effects on import**: engines/models load lazily. `import voicecli` is instant.
- **`__all__` is the contract**: anything not in `__all__` is private. Other projects depend only on `__all__` exports.
- **Python everywhere**: backends, CLIs, agents — all Python. Frontend (React, Vue) stays TypeScript when required by the target platform.
- **`uv` for all**: `uv sync` installs; `uv tool install .` for global CLI install; cross-project deps via `uv add --editable path/to/lib`.

### Why

- Lyra can call `from voicecli import generate_async` — no subprocess, no HTTP server.
- imageCLI, voiceCLI become Lyra skills with zero glue code.
- Uniform patterns across projects reduce context-switching overhead.
- Library callers get type-checked, IDE-navigable APIs. Shell callers get the same logic via CLI.

---

## Context

New personal AI agent engine, inspired by the analysis of 4 reference projects:

- **OpenClaw** (Node.js) — hub-and-spoke, 50+ channels, filesystem memory
- **NullClaw** (Zig) — 678KB, 1MB RAM, ultra-minimal
- **NanoBot** (Python) — 4k lines, educational, clean skeleton
- **OpenFang** (Rust) — 32MB, autonomous OS agent, knowledge graph, 16 security layers

Goal: take the best of each. Lightweight like NullClaw, feature-rich like OpenClaw, auditable like OpenFang, readable like NanoBot. With a persistent identity.

---

## Hardware Infrastructure

### Machine 1 — Hub (main machine)

| Spec | Value |
|------|-------|
| CPU | AMD Ryzen 7 5800X |
| RAM | 32GB |
| GPU | RTX 3080 10GB VRAM |
| OS | Ubuntu Server 24.04 LTS (dual boot Windows, default Linux) |
| Access | SSH from Machine 2 |

**Role**: Central hub, channels, database, TTS, embeddings. Never shuts down.

**VRAM Budget**:
- TTS voicecli (Qwen): ~5GB
- Embeddings (nomic-embed-text): ~0.5GB
- **Total: ~5.5GB / 10GB** → 4.5GB headroom

**STT Whisper VRAM** (via voicecli library, faster-whisper under the hood, float16 on CUDA):

| Model (`STT_MODEL_SIZE`) | VRAM | Notes |
|--------------------------|------|-------|
| `tiny` | ~0.2GB | Fastest, low accuracy |
| `small` | ~0.5GB | Good balance |
| `medium` | ~1.5GB | Higher accuracy |
| `large-v3-turbo` | ~3.0GB | **Default** — best accuracy/speed ratio |
| `large-v3` | ~3.0GB | Best accuracy, slowest |

Default (`large-v3-turbo`) adds ~3GB → total **~8.5GB / 10GB** with 1.5GB headroom.

**STT env vars**: `STT_MODEL_SIZE` (default: `large-v3-turbo`), `STT_DEVICE` (default: `auto`), `STT_COMPUTE_TYPE` (default: `auto`).
**Personal vocab**: loaded automatically from `~/.voicecli/voicecli.vocab` (shared with voicecli dictate daemon).

### Machine 2 — AI Server

| Spec | Value |
|------|-------|
| CPU | AMD Ryzen 7 9800X3D (96MB L3 V-Cache) |
| RAM | 32GB |
| GPU | RTX 5070Ti 16GB VRAM |
| OS | Windows (managed via SSH) |

**Role**: Heavy LLM on demand. Powered on as needed.

**VRAM Budget**:
- LLM Qwen 2.5 14B Q6_K: ~11GB
- or Gemma 3 27B Q4: ~15GB

**CPU Advantage**: The 9800X3D achieves ~30-40 tok/s in CPU-only inference thanks to 3D V-Cache (96MB L3). Good fallback if GPU is busy.

**LLM**: Ollama (ease of use) or llama.cpp server (max performance) — both expose an OpenAI-compatible API.

---

## Software Architecture

### Overview

```
Telegram ──▶ tg_inbound Queue ──┐
                                 ├──▶ InboundBus (staging) ──▶ Hub ──▶ resolve_binding()
Discord  ──▶ dc_inbound Queue ──┘         (bounded 100)        │
                                                                ▼
                                                        get_or_create_pool()
                                                                │
                                                                ▼
                                                        agent.process(msg, pool)
                                                                │
                                               ┌────────────────┴────────────────┐
                                               │                                 │
                                      tg_outbound Queue                dc_outbound Queue
                                      OutboundDispatcher               OutboundDispatcher
                                               │                                 │
                                          Telegram                           Discord
```

**Adapter registry** (`dict[tuple[Platform, str], ChannelAdapter]`) — keyed by `(platform, bot_id)`. Multiple bots per platform are supported; each registers independently via `hub.register_adapter(Platform.TELEGRAM, bot_id, adapter)`. The OutboundDispatcher routes responses back to the originating channel.

### Module Layout

After the Phase 1b refactoring, every module is ≤300 LOC. Key decomposition:

| Domain | Main module | Extracted modules |
|--------|------------|-------------------|
| **Hub** | `hub.py` | `hub_outbound.py`, `message_pipeline.py`, `audio_pipeline.py`, `pool_manager.py`, `hub_rate_limit.py`, `hub_protocol.py` |
| **Agent** | `agent.py` (AgentBase ABC) | `agent_config.py` (Agent dataclass), `agent_builder.py`, `agent_loader.py`, `agent_models.py`, `agent_plugins.py` |
| **Pool** | `pool.py` | `pool_processor.py` (debounce/cancel/dispatch), `pool_manager.py` (lifecycle), `pool_observer.py` (turn logging) |
| **Commands** | `command_router.py` | `builtin_commands.py`, `workspace_commands.py`, `session_commands.py` |
| **Memory** | `memory.py` | `memory_freshness.py`, `memory_schema.py`, `memory_types.py` |
| **Auth** | `auth.py` (config parsing) | `authenticator.py` (Authenticator), `guard.py` (GuardChain), `auth_store.py` |
| **Agent Store** | `agent_store.py` | `agent_seeder.py` (TOML → DB import) |
| **Outbound** | `outbound_dispatcher.py` | `outbound_errors.py` |
| **Telegram** | `telegram.py` (adapter shell) | `telegram_inbound.py`, `telegram_outbound.py`, `telegram_normalize.py`, `telegram_audio.py`, `telegram_formatting.py` |
| **Discord** | `discord.py` (adapter shell) | `discord_inbound.py`, `discord_outbound.py`, `discord_normalize.py`, `discord_audio.py`, `discord_audio_outbound.py`, `discord_formatting.py`, `discord_threads.py`, `discord_voice.py`, `discord_voice_commands.py` |
| **Bootstrap** | `multibot.py` | `multibot_stores.py`, `multibot_wiring.py` |
| **Shared** | `adapters/_shared.py` | Common adapter utilities (typing control, etc.) |

### The Bus

**Per-channel queues** (#126, completed): each channel adapter has its own bounded inbound queue → feeds a shared staging queue → Hub consumes and routes. Outbound has a symmetric per-channel queue + OutboundDispatcher.

**Backpressure**: when the staging queue is full, the adapter sends an immediate acknowledgment ("message received, ~Xs wait") then performs a blocking `await bus.put()` until a slot frees up.

**Unified message format:**
```python
@dataclass(frozen=True)
class InboundMessage:
    id: str
    platform: str               # "telegram" | "discord" | ...
    bot_id: str                 # bot identifier — multiple bots per platform supported
    scope_id: str               # canonical routing scope (computed by adapter)
    user_id: str                # canonical sender ID (rate-limiting, pairing)
    user_name: str
    is_mention: bool
    text: str                   # normalized plain text (markup stripped)
    text_raw: str               # original text with platform markup
    attachments: list[Attachment]
    reply_to_id: str | None
    thread_id: str | None
    timestamp: datetime
    locale: str | None
    trust: Literal["user", "system"]
    platform_meta: dict         # platform-specific routing data (chat_id, guild_id, …)
```

### Bindings (routing table)

Rule: `(platform, bot_id, scope_id)` → `(agent, pool_id)`

Scope extraction:
- Telegram DM / group → `chat:{chat_id}`
- Telegram forum topic → `chat:{chat_id}:topic:{topic_id}`
- Discord thread → `thread:{thread_id}`
- Discord channel → `channel:{channel_id}`

Examples:
- Telegram chat 555 → agent `lyra`, pool `telegram:main:chat:555`
- Discord thread 888 → agent `lyra`, pool `discord:main:thread:888`
- Wildcard `*` possible for an entire platform/bot

> **Note:** `"main"` is the legacy single-bot sentinel used in the examples above. In multi-bot mode, `"main"` is replaced with the configured `bot_id` (e.g., `"lyra"`, `"aryl"`), so the pool ID becomes `telegram:lyra:chat:555`.

### Multi-Bot Architecture

Lyra supports N bots per platform within a single process. The key structures that make this work:

**Adapter registry** — `dict[(Platform, bot_id), ChannelAdapter]`

Each bot registers independently at startup via `hub.register_adapter(Platform.TELEGRAM, bot_id, adapter)`. The hub routes inbound messages to the correct adapter, and the `OutboundDispatcher` verifies `(platform, bot_id)` before sending any response. A response can never be delivered by the wrong bot.

**Routing key** — `RoutingKey(platform, bot_id, scope_id)`

Every inbound message carries a 3-tuple routing key. Scope is extracted by the adapter:

| Platform | Context | scope_id |
|----------|---------|----------|
| Telegram | DM or group chat | `chat:{chat_id}` |
| Telegram | Forum topic | `chat:{chat_id}:topic:{topic_id}` |
| Discord | Thread | `thread:{thread_id}` |
| Discord | Channel | `channel:{channel_id}` |

`bot_id` is the string defined in `config.toml` (e.g., `"lyra"`, `"aryl"`). A unique Pool is created for every unique `(platform, bot_id, scope_id)` combination — two bots talking to the same user in the same channel each have completely isolated conversation history and session state.

**Per-agent vs shared resources**

| Resource | Scope |
|----------|-------|
| ProviderRegistry | Per-agent |
| SmartRoutingDecorator | Per-agent |
| Memory namespace (SQLite) | Per-agent |
| System prompt / persona | Per-agent |
| CliPool (subprocess pool) | Shared across all agents |
| asyncio event loop | Shared |
| Inbound / outbound bus | Shared |

**Discord thread ownership (cross-bot silence)**

When multiple Discord bots share a server, each adapter maintains a `_owned_threads: set[int]`. When a bot creates a thread (via `auto_thread = true`), the thread ID is added to its set. On every inbound message in a thread, the adapter checks ownership: if the thread is not in `_owned_threads` and the bot was not directly mentioned, the message is silently dropped. Direct `@mention` always bypasses the check. This prevents two bots from responding to the same message.

**Telegram webhook routing**

Each Telegram bot is assigned a distinct webhook endpoint `/webhooks/telegram/{bot_id}`. The path parameter routes the update directly to the correct adapter — no secondary dispatch needed.

See [MULTI-BOT.md](MULTI-BOT.md) for the full configuration reference and step-by-step setup guide.

### Discussion Pools

One pool per conversation scope. Contains:
- Conversation history (automatically compacted via `compact()` at 80% of context window)
- Session identity: `session_id` (UUID), `user_id`, `medium`, `session_start`, `message_count`
- Session state (multi-turn commands)
- Assigned agent
- `asyncio.Task` (`_process_loop`) — sequential within scope, parallel across scopes

### Agents

**Model: stateless singleton.** An agent is an immutable config (prompt, permissions, namespace). All mutable state lives in the Pool. No race condition since the agent never writes to `self.*`.

Each agent owns (immutable):
- Its own system prompt / persona (seeded as `IDENTITY_ANCHOR` in L3 on first boot; loaded from L3 on subsequent boots)
- Isolated memory namespace in SQLite
- Declared skill permissions
- Dedicated file workspace

```python
class Agent:
    name: str                    # immutable
    system_prompt: str           # immutable
    memory_namespace: str        # immutable — filters SQLite queries
    permissions: list[str]       # immutable

    async def process(self, msg: InboundMessage, pool: Pool) -> Response:
        # pool contains all mutable state (history, session)
        ...
```

**Memory wiring** (#83): `Hub.set_memory(manager)` injects a `MemoryManager` into all registered agents. `MemoryManager` wraps `AsyncMemoryDB` (roxabi-vault) and exposes:
- `recall(user_id, ns, first_msg, token_budget)` — freshness-aware cross-session context (`[MEMORY]` + `[PREFERENCES]` blocks)
- `upsert_session(snap, summary)` — flush session summary to L3 after eviction
- `upsert_concept()` / `upsert_preference()` — background extraction tasks
- `compact()` — writes partial L3 snapshot + truncates context to a 10-turn tail when the window hits 80% of `COMPACT_THRESHOLD` (200k tokens)

Multiple agents run simultaneously on different pools. A single agent (e.g., `lyra`) serves multiple pools without duplication.

> **Upgrade path Phase 2**: if the atomic SLMs require sub-millisecond recall of user preferences, add `agent_state: dict` to the Pool (one line). Zero refactoring of the agent model.

---

## Memory Layer (5 levels)

| Level | Name | Nature | Lifetime | Phase 1 Status |
|-------|------|--------|----------|----------------|
| 0 | **Working memory** | Active context window (current messages) | Volatile | ✅ Built — L0 compaction in #83 |
| 1 | **Session memory** | Raw turn logging (TurnStore) + session state | Session duration | ✅ Built — raw turn logging in #67 |
| 2 | **Episodic** | Dated Markdown, immutable, human-auditable | Permanent | Deferred (Phase 2) |
| 3 | **Semantic** | SQLite + FTS5/BM25 + fastembed + sqlite-vec | Permanent | ✅ Built (#78/#81/#82) |
| 4 | **Procedural** | Learned skills, memorized patterns, preferences | Permanent | Deferred (Phase 3) |

### Level 0 — Working memory ✅
- Active context window, volatile, managed by the LLM
- `AgentBase.compact()` triggers when `sdk_history` exceeds 80% of `COMPACT_THRESHOLD` (200k tokens):
  1. Calls `_summarize_session()` → LLM summary of conversation so far
  2. Writes partial L3 snapshot via `MemoryManager.upsert_session(..., status="partial")`
  3. Replaces `sdk_history` with `[summary_turn] + last 10 turns` in-place
- `AgentBase.flush_session()` runs on pool eviction / explicit disconnect → full L3 write + background concept/preference extraction

### Level 1 — Session memory ✅
- **TurnStore** (`src/lyra/core/turn_store.py`) — raw turn logging to `~/.lyra/turns.db` (SQLite)
- Every user and assistant turn persisted with `pool_id`, `session_id`, `role`, `content`, platform message IDs
- Separate DB from roxabi-vault to avoid write contention
- Fire-and-forget writes via `asyncio.create_task` — never blocks message processing
- Query interface: `get_session_turns()`, `get_pool_turns()`, `get_user_turns()`

### Level 2 — Episodic *(Phase 2)*
- Dated Markdown files (`memory/YYYY-MM-DD.md`)
- Immutable, auditable, human-readable
- Each interaction logged with timestamp + channel

### Level 3 — Semantic ✅
- SQLite + `aiosqlite` (non-blocking)
- BM25 via FTS5 built-in SQLite (keywords, proper nouns, dates)
- Embeddings via `fastembed` ONNX + `sqlite-vec` (conceptual similarity, non-blocking)
- Hybrid search BM25 + cosine similarity
- **Mandatory URL indexing from the initial schema**: `normalized_url` and `resolved_url` indexed columns → O(1) deduplication via SQL, no O(n) scan in Python (lesson from 2ndBrain #129)

### Level 4 — Procedural *(Phase 3)*
- Dynamically learned skills, memorized patterns
- Persistent user preferences per agent
- Stored in SQLite, updated via automatic consolidation

### Consolidation & time-decay
- Automatic compaction: summary of old turns → semantic level
- Time-decay: decreasing relevance score (contextual noise reduction)
- Entity extraction: people, dates, places, concepts → optional graph (`networkx` / `kuzu`)

---

## Tools / Skills Layer

- `SKILL.md` manifest per skill: capabilities, permissions, dependencies
- Registry built at startup by the hub
- Sandboxing: limited env variables, restricted filesystem, network whitelist
- Progressive streaming of long responses (chunked pattern)

### External tool integration (ADR-010)

External CLIs (Google Workspace, VoiceCLI, ImageCLI, scraper) follow a **3-layer pattern: Install, Wrap, Declare**.

| Layer | What | Where |
|-------|------|-------|
| **Install** | CLI binary on PATH via `setup.sh` / package manager | Host machine |
| **Wrap** | Thin roxabi-plugins skill (`SKILL.md` only, no code) | `roxabi-plugins/` repo |
| **Declare** | Agent TOML declares tool access (Bash allowlist now, MCP later) | `lyra/` repo |

No forking, no vendoring. Upstream maintains the CLI; we maintain the skill wrapper and agent config.

See `docs/architecture/adr/010-external-tool-integration-pattern.mdx` for full rationale.

---

## Security Layer

- **Prompt injection guard**: content validation before agent context
- **Immutable audit trail**: hash-chained log of all actions
- **Least privilege**: each skill declares and justifies its permissions
- **Third-party skill signing**: integrity verification at load time

---

## Features

- **24/7 Autonomy**: embedded scheduler (no external cron), temporal triggers and webhooks
- **In-memory session state**: multi-turn context per pool, idle-timeout liveness detection · raw turn logging to SQLite (TurnStore, #67 ✅)
- **Auto compaction**: summary of old turns → semantic memory (virtuous loop)
- **Multi-channel**: Telegram first, Discord without touching the core
- **Multi-agent**: routing via bindings, isolated workspaces

---

## Technical Stack

### Machine 1 (Hub)

| Component | Lib |
|-----------|-----|
| Runtime | Python 3.12 + asyncio |
| Dependencies | uv |
| Validation | pydantic |
| Telegram | aiogram v3 (asyncio-native, tracks Bot API same-day) |
| Discord | discord.py v2 (gateway WebSocket, on_message) |
| Webhook server | FastAPI + uvicorn (Telegram webhook endpoint) |
| HTTP client | httpx[asyncio] |
| SQLite async | aiosqlite |
| BM25 | FTS5 (built-in SQLite) |
| Vector search | sqlite-vec + fastembed ONNX |
| TTS | voicecli (Qwen-fast) · OGG/Opus output · waveform · Discord voice bubble ✅ |
| STT | voicecli library (faster-whisper, `large-v3-turbo`) |
| Embeddings | fastembed ONNX (nomic-embed-text) + sqlite-vec |
| Process mgmt | supervisord + systemd |
| Internal API | FastAPI |

### Machine 2 (AI Server)

| Component | Lib |
|-----------|-----|
| LLM runtime | Ollama (or llama.cpp server) |
| Exposed API | FastAPI `/llm` |
| Protocol | OpenAI-compatible |

### Inter-machine Communication

`httpx` async on local network (HTTP/2). No gRPC — unnecessary at this throughput.

```python
client = AsyncOpenAI(
    base_url="http://machine2:8080/v1",
    api_key="local"
)
```

---

## Key Decisions

### Architectural decisions (have real alternatives)

- **Python + asyncio** — Go/Rust/Zig/Node eliminated. Python AI ecosystem is unbeatable, asyncio is sufficient for 1-5 I/O-bound users.
- **2 machines** — Machine 1 autonomous (hub + TTS + embeddings), Machine 2 on demand (heavy LLM). Eliminates VRAM contention.
- **Cloud LLM by default** — LlmProvider protocol (#123 ✅) with two drivers: `ClaudeCliDriver` (CLI subprocess) and `AnthropicSdkDriver` (direct API). Smart routing (#134 ✅) selects model by complexity. Local LLM on Machine 2 = Phase 2 (NATS worker).
- **SQLite** — No Postgres. SQLite + WAL mode + `aiosqlite` amply covers personal use.

### Resolved decisions (Phase 1b completions)

- **Response routing** — Adapter registry: `dict[str, ChannelAdapter]` in the Hub. Each adapter registers at startup. The hub routes the response via `adapter_registry[msg.channel].send()`.
- **Pool/agent: stateless singleton** — An agent = immutable config shared across all pools. All mutable state lives in the Pool. No duplication, no race condition.
- **Backpressure: bounded queue (100)** — `asyncio.Queue(maxsize=100)`. Queue full → immediate acknowledgment + blocking `await put()`.
- **Per-channel queues** (#126) — Each channel adapter has an isolated inbound/outbound queue pair. InboundBus staging queue feeds the Hub. OutboundDispatcher per channel handles responses.
- **scope_id replaces user_id in RoutingKey** (#125) — `RoutingKey(platform, bot_id, scope_id)`. Scope extracted from platform context: `chat:NNN`, `thread:NNN`, `channel:NNN`, etc.
- **fastembed ONNX replaces sentence-transformers** (#82) — Non-blocking ONNX runtime, no `run_in_executor` needed. Hybrid BM25 (FTS5) + cosine (sqlite-vec).
- **LLM circuit breaker** (#104) — Timeout + retry logic for Anthropic SDK calls. Graceful degradation on failure.
- **LlmProvider protocol** (#123 ✅) — Multi-driver abstraction: `AnthropicSdkDriver`, `ClaudeCliDriver`. Smart routing (#134 ✅) selects model by complexity. OllamaDriver planned for Phase 2.
- **Auth: Authenticator + GuardChain** (#151 ✅, refactored in #313/#314) — Per-adapter auth with trust levels (owner/trusted/public/blocked). Config-driven via TOML. Originally a monolithic `AuthMiddleware`; refactored into `Authenticator` (identity resolver in `authenticator.py`) and `GuardChain` (composable guard pipeline in `guard.py`). Note: `[admin].user_ids` grants cross-platform admin commands to those users across ALL bots, whereas per-bot `owner_users` in `[[auth.telegram_bots]]` / `[[auth.discord_bots]]` sets the trust level for that specific bot only.
- **RoutingContext + outbound verification** (#152 ✅) — Every outbound response carries a RoutingContext; adapters verify channel + bot_id before sending.
- **PoolContext protocol** (#204 ✅) — Decouples Pool from Hub via a protocol interface.
- **TTL eviction for Hub.pools** (#205 ✅) — Prevents memory leak from stale pools.
- **Message normalization** (#139 ✅) — Full bus envelope: InboundMessage, OutboundMessage, InboundAudio, OutboundAudioChunk, OutboundAttachment. Per-adapter render functions.
- **Runtime agent config** (#135 ✅) — Live tuning via `!config` command, no restart needed.
- **Voice STT** (#80 ✅) — STTService delegates to voicecli library (faster-whisper + personal vocab from `~/.voicecli/voicecli.vocab`), InboundAudioBus, audio consumer loop in Hub.
- **Typing indicator redesign** (#229 ✅) — `TelegramAdapter` and `DiscordAdapter` start typing at message receipt (`_on_message`); long-running requests keep the indicator alive via a background task. Typing is cancelled **after** the last chunk is confirmed sent — in `send()` after the send loop, and in `send_streaming()` after the final edit — ensuring the indicator stays active until the message is visible.
- **Outbound send reliability** — `OutboundDispatcher` retries transient send failures (network errors, 5xx, rate-limit 429) up to 3 times with exponential backoff (1 s / 2 s / 4 s). After all retries are exhausted, the user receives a plaintext error notification (`"⚠️ I encountered an error sending my response. Please try again."`). When the platform circuit breaker is open, the user is notified once per 60 s per scope (`"⚠️ I'm temporarily unavailable. Please try again in a moment."`). Non-retryable errors (4xx client errors) fail immediately.
- **Intermediate turns** (`show_intermediate` ✅) — `on_intermediate` callback threaded through LlmProvider decorators into `CliPool._read_until_result`. When `show_intermediate = true` in agent TOML, each intermediate CLI turn is dispatched to the user as a `⏳`-prefixed message.
- **`/clear` resets backend session** ✅ — `pool.reset_session()` is now async and delegates to `CliPool.reset()` via `_session_reset_fn`, clearing both in-memory history and the CLI process session.
- **`is_backend_alive()` + idle-timeout liveness** ✅ — `Agent` and `CliPool` expose `is_backend_alive(pool_id)` / `is_alive(pool_id)`. `CliPool` uses per-read idle timeouts with retry (3×) instead of a global deadline — each read resets the timer, and process death is detected via EOF between retries. The outer `Pool` timeout is disabled (`None`) since `CliPool` handles liveness directly.
- **SimpleAgent `runtime_config` wiring** ✅ — `_build_router_kwargs()` injects `runtime_config_holder` and `runtime_config_path`, making `/config` functional on the `claude-cli` backend.
- **`[model].cwd` and `[workspaces]`** ✅ — `ModelConfig.cwd` sets a fixed working directory for the Claude subprocess for a given agent. `[workspaces]` in agent TOML registers named directory shortcuts; each key becomes a `/keyname` slash command that stores a per-pool cwd override in `CliPool._cwd_overrides`. Switching workspace clears pool history and kills/respawns the Claude subprocess with the new cwd.
- **Reduced Phase 1 memory scope** — Level 0 (working, L0 compaction ✅ #83) + Level 3 (semantic ✅ #78/#81/#82). Levels 1, 2, 4 added when the real need arises.
- **Memory agent integration** (#83 ✅) — `MemoryManager` wired into Pool identity fields, AgentBase lifecycle (`build_system_prompt`, `compact`, `flush_session`, `_schedule_extraction`), and Hub (`set_memory`, `_memory_tasks`, shutdown drain). Identity anchor seeded in L3 on first boot. FTS isolated per user via `namespace:user_id` sub-namespace. 7 slices delivered: Pool identity, MemoryManager infra, identity anchor, session flush, compaction, cross-session recall, concept/preference extraction.
- **AgentStore** (#268 ✅) — SQLite-backed agent registry (`~/.lyra/auth.db`). TOML files are seed sources only — imported via `lyra agent init`. Runtime reads from DB. CLI: `init`, `list`, `show`, `edit`, `validate`, `assign`, `unassign`, `delete`. In-memory cache warmed at `connect()` — no per-message file I/O. Includes `tts_json`/`stt_json` columns for per-agent TTS/STT config (serialized from TOML `[tts]`/`[stt]` sections, deserialized into `AgentTTSConfig`/`AgentSTTConfig`). See ADR-024.
- **Raw turn logging** (#67 ✅) — `TurnStore` (`src/lyra/core/turn_store.py`) persists every user + assistant turn to `~/.lyra/turns.db` (SQLite, separate from vault). Fire-and-forget writes via `asyncio.create_task`. Query: `get_session_turns()`, `get_pool_turns()`, `get_user_turns()`. This is the L1 memory layer.
- **Retryable LlmResult** (#276 ✅) — `LlmResult` carries a `retryable: bool` flag. Non-retryable errors (auth failures, invalid requests) skip the retry/backoff loop in decorators.
- **Hub command sessions** (#99 ✅) — Session command layer: `SessionCommandHandler` protocol, `SessionCommandEntry` registry in `CommandRouter`. `/add` (scrape → LLM summary → vault write), `/explain` (scrape → LLM plain-language explanation), `/summarize` (scrape → LLM bullet points), `/search` (vault FTS). Bare URL messages auto-rewritten to `/add <url>`. Scraping via `web-intel:scrape` subprocess; vault via `vault` CLI. `session_helpers.py` provides `scrape_url`, `vault_add`, `vault_search` async wrappers. `session_commands.py` contains the four command handlers. `AnthropicAgent` wired with a session driver for isolated LLM calls (no pool history pollution). `plugins/search/` plugin implements `/search`.

### External tool integration

- **Install, Wrap, Declare** (ADR-010) — see [Tools / Skills Layer](#external-tool-integration-adr-010) above.

### Deferred Gaps (Phase 2)

- **Machine 2 / local LLM** — OllamaDriver in #123 will add the driver; NATS worker for Machine 2 is Phase 2 (#51). Circuit breaker for remote LLM: #23.
- **Machine 1 VRAM under load** — Measure with `nvidia-smi` before planning Phase 2 SLMs.
- **Memory levels 2, 4** — Episodic Markdown logs (L2), procedural seeds (L4) deferred. Add when real need arises. (L1 raw turn logging shipped in #67.)

### Technical constraints (not decisions, facts)

- **`aiosqlite` mandatory** — Synchronous SQLite in an asyncio event loop blocks everything. Non-negotiable.
- **No gRPC** — `httpx` HTTP/2 is sufficient for inter-machine throughput at personal use scale.
- **Machine 1 never shuts down** — hub, channels, database, TTS. Must be available 24/7.

---

## Phase 1 — Scope

What is built in Phase 1 / 1b:
- Hub: per-channel queues + bindings + pools + adapter registry (#112 epic ✅)
- Memory level 0 (working, L0 compaction ✅ #83) + level 3 (semantic ✅: #78/#81/#82)
- Telegram + Discord adapters (✅)
- LLM: Claude CLI subprocess (✅), Anthropic SDK driver (#76 ✅), LlmProvider protocol (#123 ✅), smart routing (#134 ✅)
- Agent identity + persona (#75 ✅), runtime config (#135 ✅)
- Message normalization (#139 ✅): InboundMessage, OutboundMessage, InboundAudio, OutboundAudioChunk, OutboundAttachment
- Auth: Authenticator + GuardChain (#151 ✅, refactored #313/#314), RoutingContext + outbound verification (#152 ✅)
- Voice: STTService + STTConfig (#80 ✅), InboundAudioBus, audio consumer loop · TTS shipped: OGG/Opus (ffmpeg libopus, 48kHz mono), `SynthesisResult` with `duration_ms` + `waveform_b64` (256-byte amplitude array), language ISO→Qwen normalization, Discord `IS_VOICE_MESSAGE` (8192) flag for native voice bubble
- Hub hardening: PoolContext protocol (#204 ✅), TTL eviction (#205 ✅), async I/O audio loop (#203 ✅)
- DX: complexity/size limits (#196 ✅), pytest-cov + coverage gate (#211 ✅)
- Security: hmac.compare_digest (#212 ✅), two-tier /health (#207 ✅), symlink plugin_loader fix (#215 ✅)
- UX: typing indicator redesign (#229 ✅), intermediate turns (`show_intermediate`) ✅, `/clear` session reset ✅
- Hub command sessions (#99 ✅): `/add`, `/explain`, `/summarize`, `/search` — session command layer with isolated LLM calls, scrape + vault integration, bare URL auto-rewrite
- AgentStore (#268 ✅): SQLite-backed agent registry (`~/.lyra/auth.db`), CLI overhaul (`init`, `list`, `show`, `edit`, `validate`, `assign`, `delete`)
- Raw turn logging (#67 ✅): TurnStore — L1 memory layer, conversation audit trail to `~/.lyra/turns.db`
- Retryable LlmResult (#276 ✅): non-retryable errors skip retry/backoff loop

**Phase 1b tail: complete.** All items shipped.

**Post-Phase 1b: architecture refactoring shipped.**
- Module decomposition (#294–#312): all core, adapter, and bootstrap modules decomposed to ≤300 LOC
- Auth refactoring (#313/#314): `AuthMiddleware` → `Authenticator` + `GuardChain`
- Deduplication: 8 cross-codebase patterns consolidated
- Timeout hardening (#317): reaper process + timeout system hardened
- Session resumption (#318): `session_id` + `reply_message_id` wired for resumption
- Dead code removal: `event_bus.py`, `bootstrap/legacy.py` deleted

What is **explicitly excluded from Phase 1**:
- Memory levels 2 (episodic), 4 (procedural) — added when the real need arises (L1 raw turn logging shipped in #67)
- Atomic SLMs (Phase 3)
- Cognitive meta-language between SLMs
- Knowledge graph (optional level 4)
- Machine 2 / local LLM (Phase 2, NATS-based)
- Hash-chained audit trail (Phase 4 — unnecessary for personal use)

## Phase 2 — Atomic SLM & Cognitive Meta-language

> **Strict prerequisite**: stable Phase 1 hub + validated Machine 1 VRAM budget.

**Current Machine 1 VRAM budget**: TTS ~5GB + embeddings ~0.5GB = **5.5GB / 10GB**. Headroom: 4.5GB.

Running multiple SLMs in parallel within this headroom is possible but requires real measurement before committing.

### Atomic SLMs

Reserve the large LLM only for generation. Everything else → small specialized models.

| Task | Target size | Target latency |
|------|------------|----------------|
| Routing / intent triage | ~1-3B | <50ms |
| Memory relevance scoring | ~1B | <30ms |
| Entity extraction (NER) | ~3B | <100ms |
| Skill selection / planner | ~3-7B | <200ms |

**Expected impact**: 80-90% of messages routed without the full LLM. Cost /10, latency /5 on simple cases.

### Cognitive Meta-language

SLMs exchange `CognitiveFrame` — compact structures, not natural language:

```python
@dataclass
class CognitiveFrame:
    intent: str
    entities: list[str]
    context_refs: list[str]
    skill_path: list[str]
    confidence: float
    emotional_tone: str | None
    metadata: dict
```

**Cognitive flow**: message → routing SLM → memory SLM → planner SLM → skills → LLM (if needed) → NER SLM → memory update.

## Current Status

**Phase 1b complete. Architecture refactoring complete.**

**Phase 1b shipped**: #135 (runtime config ✅), #134 (smart routing ✅), #80 (voice STT ✅), #139 (message normalization ✅), #123 (LlmProvider ✅), #151 (auth ✅), #152 (routing ✅), #83 (memory integration ✅), #99 (hub command sessions ✅), voice TTS ✅ (OGG/Opus · waveform · Discord voice bubble · /voice routes through LLM), #268 (AgentStore — SQLite-backed agent registry ✅), #67 (raw turn logging — TurnStore L1 ✅), #276 (retryable flag on LlmResult ✅)

**Architecture refactoring shipped** (2026-03-16/17):
- Module decomposition: hub.py → `hub.py` + `message_pipeline.py` + `audio_pipeline.py` + `pool_manager.py` + `hub_rate_limit.py` + `hub_protocol.py` (#294–#312)
- Adapter decomposition: `discord.py` → 8 focused modules (`discord_inbound.py`, `discord_outbound.py`, `discord_audio.py`, `discord_formatting.py`, etc.) (#296/#311); `telegram.py` → 5 focused modules (#297)
- Core decomposition: `agent.py` → `agent.py` + `agent_builder.py` + `agent_config.py` + `agent_loader.py` + `agent_models.py` + `agent_plugins.py` (#295/#306); `pool.py` → `pool.py` + `pool_processor.py` (#300/#309); `command_router.py` → `command_router.py` + `builtin_commands.py` + `workspace_commands.py` (#298/#312); `memory.py` → `memory.py` + `memory_freshness.py` + `memory_schema.py` + `memory_types.py`; `agent_store.py` → `agent_store.py` + `agent_seeder.py` (#304/#308)
- Auth split: `AuthMiddleware` → `Authenticator` (identity resolver) + `GuardChain` (composable guard pipeline) (#313/#314)
- 8-pattern deduplication across adapters + core — shared adapter code in `adapters/_shared.py`
- Removed dead abstractions: `event_bus.py` (EventBus pub/sub), `bootstrap/legacy.py`
- **#317** — Harden timeout system and reaper process
- **#318** — Wire `session_id` + `reply_message_id` for session resumption

**Next**: Phase 2 (#60) — NATS introduction + Machine 2 coordination, or #136 (multi-bot registry upgrade, blocked by #79).

See [ROADMAP.md](ROADMAP.md) for the full backlog and priorities.
