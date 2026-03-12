# Lyra — Architecture & Decisions

> Living document. Updated as decisions are made.
> Last updated: 2026-03-12 (Phase 1b completions: per-channel queues #126, fastembed #82, scope_id #125, LLM circuit breaker #104)

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

**STT Whisper VRAM** (faster-whisper, float16 on CUDA):

| Model (`STT_MODEL_SIZE`) | VRAM | Notes |
|--------------------------|------|-------|
| `tiny` | ~0.2GB | Fastest, low accuracy |
| `small` | ~0.5GB | **Default** — good balance |
| `medium` | ~1.5GB | Higher accuracy |
| `large-v3` | ~3.0GB | Best accuracy, slowest |

Default (`small`) adds ~0.5GB → total **~6GB / 10GB** with 4GB headroom.

**STT env vars**: `STT_MODEL_SIZE` (default: `small`), `STT_DEVICE` (default: `auto`), `STT_COMPUTE_TYPE` (default: `auto`).

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

**Adapter registry** (`dict[str, ChannelAdapter]`) — each adapter registers at startup via `hub.register_adapter("telegram", adapter)`. The OutboundDispatcher routes responses back to the originating channel.

### The Bus

**Per-channel queues** (#126, completed): each channel adapter has its own bounded inbound queue → feeds a shared staging queue → Hub consumes and routes. Outbound has a symmetric per-channel queue + OutboundDispatcher.

**Backpressure**: when the staging queue is full, the adapter sends an immediate acknowledgment ("message received, ~Xs wait") then performs a blocking `await bus.put()` until a slot frees up.

**Unified message format:**
```python
@dataclass
class Message:
    id: str
    platform: Platform          # Platform.TELEGRAM | Platform.DISCORD
    bot_id: str                 # "main" (one bot per platform)
    user_id: str                # canonical sender ID (rate-limiting, pairing)
    platform_context: ...       # TelegramContext | DiscordContext (scope routing)
    content: MessageContent     # TextContent | ImageContent | AudioContent
    type: MessageType
    timestamp: datetime
    metadata: dict

    def extract_scope_id(self) -> str:
        """Return conversation scope: chat:NNN, thread:NNN, channel:NNN, …"""
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

### Discussion Pools

One pool per conversation scope. Contains:
- Conversation history (automatically compacted)
- Session state (multi-turn commands)
- Assigned agent
- `asyncio.Task` (`_process_loop`) — sequential within scope, parallel across scopes

### Agents

**Model: stateless singleton.** An agent is an immutable config (prompt, permissions, namespace). All mutable state lives in the Pool. No race condition since the agent never writes to `self.*`.

Each agent owns (immutable):
- Its own system prompt / persona
- Isolated memory namespace in SQLite
- Declared skill permissions
- Dedicated file workspace

```python
class Agent:
    name: str                    # immutable
    system_prompt: str           # immutable
    memory_namespace: str        # immutable — filters SQLite queries
    permissions: list[str]       # immutable

    async def process(self, msg: Message, pool: Pool) -> Response:
        # pool contains all mutable state (history, session)
        ...
```

Multiple agents run simultaneously on different pools. A single agent (e.g., `lyra`) serves multiple pools without duplication.

> **Upgrade path Phase 2**: if the atomic SLMs require sub-millisecond recall of user preferences, add `agent_state: dict` to the Pool (one line). Zero refactoring of the agent model.

---

## Memory Layer (5 levels)

| Level | Name | Nature | Lifetime | Phase 1 Status |
|-------|------|--------|----------|----------------|
| 0 | **Working memory** | Active context window (current messages) | Volatile | ✅ Built — L0 compaction in #83 |
| 1 | **Session memory** | Multi-turn session state per pool | Session duration | Deferred (Phase 2) |
| 2 | **Episodic** | Dated Markdown, immutable, human-auditable | Permanent | Deferred (Phase 2) |
| 3 | **Semantic** | SQLite + FTS5/BM25 + fastembed + sqlite-vec | Permanent | ✅ Built (#78/#81/#82) |
| 4 | **Procedural** | Learned skills, memorized patterns, preferences | Permanent | Deferred (Phase 3) |

### Level 0 — Working memory
- Active context window, volatile, managed by the LLM
- Automatically compacted when the window approaches its limit (L0 compaction: #83)

### Level 1 — Session memory *(Phase 2)*
- Multi-turn session state per pool (`asyncio.Task` per scope)
- Ongoing commands, conversation context
- Configurable timeout, cleaned up on disconnect

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
- **In-memory session state**: multi-turn context per pool, configurable timeout (persistent JSONL logs = #67, Phase 2)
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
| TTS | voicecli (Qwen-fast) |
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
- **Cloud LLM by default** — Currently Claude Code CLI subprocess (`claude-cli`). Anthropic SDK driver planned in #123 (LlmProvider protocol). Local LLM on Machine 2 = Phase 2 (NATS worker).
- **SQLite** — No Postgres. SQLite + WAL mode + `aiosqlite` amply covers personal use.

### Resolved decisions (Phase 1b completions)

- **Response routing** — Adapter registry: `dict[str, ChannelAdapter]` in the Hub. Each adapter registers at startup. The hub routes the response via `adapter_registry[msg.channel].send()`.
- **Pool/agent: stateless singleton** — An agent = immutable config shared across all pools. All mutable state lives in the Pool. No duplication, no race condition.
- **Backpressure: bounded queue (100)** — `asyncio.Queue(maxsize=100)`. Queue full → immediate acknowledgment + blocking `await put()`.
- **Per-channel queues** (#126) — Each channel adapter has an isolated inbound/outbound queue pair. InboundBus staging queue feeds the Hub. OutboundDispatcher per channel handles responses.
- **scope_id replaces user_id in RoutingKey** (#125) — `RoutingKey(platform, bot_id, scope_id)`. Scope extracted from platform context: `chat:NNN`, `thread:NNN`, `channel:NNN`, etc.
- **fastembed ONNX replaces sentence-transformers** (#82) — Non-blocking ONNX runtime, no `run_in_executor` needed. Hybrid BM25 (FTS5) + cosine (sqlite-vec).
- **LLM circuit breaker** (#104) — Timeout + retry logic for Anthropic SDK calls. Graceful degradation on failure.
- **LlmProvider protocol** (#123, in analysis) — Multi-driver abstraction: `AnthropicSdkDriver`, `ClaudeCliDriver`, `OllamaDriver`. Unblocks #83.
- **Reduced Phase 1 memory scope** — Level 0 (working, L0 compaction in #83) + Level 3 (semantic, shipped #78/#81/#82). Levels 1, 2, 4 added when the real need arises.

### External tool integration

- **Install, Wrap, Declare** (ADR-010) — see [Tools / Skills Layer](#external-tool-integration-adr-010) above.

### Deferred Gaps (Phase 2)

- **Machine 2 / local LLM** — OllamaDriver in #123 will add the driver; NATS worker for Machine 2 is Phase 2 (#51). Circuit breaker for remote LLM: #23.
- **Machine 1 VRAM under load** — Measure with `nvidia-smi` before planning Phase 2 SLMs.
- **Memory levels 1, 2, 4** — Session memory (L1), episodic Markdown logs (L2), procedural seeds (L4) deferred. Add when real need arises.

### Technical constraints (not decisions, facts)

- **`aiosqlite` mandatory** — Synchronous SQLite in an asyncio event loop blocks everything. Non-negotiable.
- **No gRPC** — `httpx` HTTP/2 is sufficient for inter-machine throughput at personal use scale.
- **Machine 1 never shuts down** — hub, channels, database, TTS. Must be available 24/7.

---

## Phase 1 — Scope

What is built in Phase 1 / 1b:
- Hub: per-channel queues + bindings + pools + adapter registry (#112 epic ✅)
- Memory level 0 (working, L0 compaction in #83) + level 3 (semantic ✅: #78/#81/#82)
- Telegram + Discord adapters (✅)
- LLM: Claude CLI subprocess (✅), Anthropic SDK driver (#76 ✅), multi-driver abstraction (#123 in analysis)
- Agent identity + persona (#75 ✅), session lifecycle (#83 in progress)

**Phase 1b tail** (in progress):
- Message normalization (#139) → LlmProvider protocol (#123) → agent integration (#83) → hub command sessions (#99)
- Independent: runtime config (#135), smart routing (#134), voice STT (#80)

What is **explicitly excluded from Phase 1**:
- Memory levels 1 (session), 2 (episodic), 4 (procedural) — added when the real need arises
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

## Current Status (Phase 1b tail)

Phase 1 core is complete. Active work on closing out the agent core:

**Critical path**: #139 (message normalization) ∥ #123 (LlmProvider) → #83 (agent integration) → #99 (hub command sessions)

**Independent**: #135 (runtime config), #134 (smart routing), #80 (voice STT)

See [ROADMAP.md](ROADMAP.md) for the full backlog and priorities.
