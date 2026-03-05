# Lyra — Architecture & Decisions

> Living document. Updated as decisions are made.
> Last updated: 2026-03-05 (ADR-010 external tool integration)

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
                    adapter_registry
                    ┌─────────────────────────────────┐
                    │ "telegram" → TelegramAdapter │
                    │ "discord"  → DiscordAdapter  │
                    └─────────────────────────────────┘
                           ▲ register()        ▲ send(response)
                           │                   │
Telegram ──┐               │                   │
Discord  ──┼──▶ asyncio.Queue(100) ──▶ Hub ──▶ resolve_binding()
Signal   ──┘     (bounded)                │
                                          ▼
                                   get_or_create_pool()
                                          │
                                          ▼
                                   agent.process(msg, pool)
                                          │
                             ┌────────────┴────────────┐
                             │                         │
                          skills                API Machine 2
                     (CPU / VRAM M1)          (heavy LLM /llm)
                             │
                             ▼
              adapter_registry[msg.channel].send(response)
```

### The Bus

`asyncio.Queue(maxsize=100)` **bounded**. Each channel adapter runs as an independent asyncio task, normalizes messages into a unified format, and pushes them into the queue. The hub consumes and routes.

**Backpressure**: when the queue is full, the adapter sends an immediate acknowledgment ("message received, ~Xs wait") then performs a blocking `await bus.put()` until a slot frees up.

### Adapter Registry (response routing)

The hub maintains a `dict[str, ChannelAdapter]` — the adapter registry. Each adapter registers at startup via `hub.register_adapter("telegram", telegram_adapter)`. When `agent.process()` returns a response, the hub calls `adapter_registry[message.channel].send(response)`.

```python
class Hub:
    adapter_registry: dict[str, ChannelAdapter]

    def register_adapter(self, name: str, adapter: ChannelAdapter) -> None:
        self.adapter_registry[name] = adapter

    async def dispatch_response(self, original_msg: Message, response: Response) -> None:
        adapter = self.adapter_registry[original_msg.channel]
        await adapter.send(original_msg, response)
```

**Unified message format:**
```python
@dataclass
class Message:
    id: str
    channel: str        # "telegram" | "discord"
    user_id: str        # canonical ID (not the raw platform ID)
    content: str | dict # text, image, audio...
    type: MessageType
    timestamp: datetime
    metadata: dict
```

### Bindings (routing table)

Rule: `(channel, user_id)` → `(agent, pool_id)`

Examples:
- Telegram + @Roxabi → agent `lyra`, pool `telegram_roxabi`
- Discord + #general → agent `assistant`, pool `discord_general`
- Wildcard `*` possible for an entire channel

### Discussion Pools

One pool per `(channel, user)`. Contains:
- Conversation history (automatically compacted)
- Session state (multi-turn commands)
- Assigned agent
- `asyncio.Lock` — sequential per user, parallel across users

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

| Level | Name | Nature | Lifetime |
|-------|------|--------|----------|
| 0 | **Working memory** | Active context window (current messages) | Volatile |
| 1 | **Session memory** | Multi-turn session state per pool | Session duration |
| 2 | **Episodic** | Dated Markdown, immutable, human-auditable | Permanent |
| 3 | **Semantic** | SQLite + BM25 + embeddings, hybrid search | Permanent |
| 4 | **Procedural** | Learned skills, memorized patterns, preferences | Permanent |

### Level 0 — Working memory
- Active context window, volatile, managed by the LLM
- Automatically compacted when the window approaches its limit

### Level 1 — Session memory
- Multi-turn session state per pool (`asyncio.Lock`)
- Ongoing commands, conversation context
- Configurable timeout, cleaned up on disconnect

### Level 2 — Episodic
- Dated Markdown files (`memory/YYYY-MM-DD.md`)
- Immutable, auditable, human-readable
- Each interaction logged with timestamp + channel

### Level 3 — Semantic
- SQLite + `aiosqlite` (non-blocking)
- BM25 via `rank-bm25` (keywords, proper nouns, dates)
- Embeddings via `sqlite-vec` (conceptual similarity)
- Hybrid search BM25 + cosine similarity
- **Mandatory URL indexing from the initial schema**: `normalized_url` and `resolved_url` indexed columns → O(1) deduplication via SQL, no O(n) scan in Python (lesson from 2ndBrain #129)

### Level 4 — Procedural
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

External CLIs (Google Workspace, VoiceCLI, scraper, image generation) follow a **3-layer pattern: Install, Wrap, Declare**.

| Layer | What | Where |
|-------|------|-------|
| **Install** | CLI binary on PATH via `setup.sh` / package manager | Machine 1 (or Machine 2) |
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
- **Session persistence**: multi-turn context per pool, configurable timeout
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
| BM25 | rank-bm25 |
| Vector search | sqlite-vec |
| TTS | voicecli (Qwen-fast) |
| Embeddings | sentence-transformers (nomic-embed-text) |
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
- **Cloud LLM by default** — Anthropic API from Machine 1. Local LLM Machine 2 = offline fallback / cost control.
- **SQLite** — No Postgres. SQLite + WAL mode + `aiosqlite` amply covers personal use.

### Resolved decisions (REVIEW.md gaps, 2026-03-02)

- **Response routing** — Adapter registry: `dict[str, ChannelAdapter]` in the Hub. Each adapter registers at startup. The hub routes the response via `adapter_registry[msg.channel].send()`.
- **Pool/agent: stateless singleton** — An agent = immutable config shared across all pools. All mutable state lives in the Pool. No duplication, no race condition.
- **Backpressure: bounded queue (100)** — `asyncio.Queue(maxsize=100)`. Queue full → immediate acknowledgment + blocking `await put()`.
- **Reduced Phase 1 memory scope** — Levels 0 (working) + 3 (semantic) only. Levels 1, 2, 4 added when the real need arises.

### External tool integration

- **Install, Wrap, Declare** (ADR-010) — see [Tools / Skills Layer](#external-tool-integration-adr-010) above.

### Deferred Gaps (Phase 2)

- **Synchronous embeddings** — `sentence-transformers` blocks the event loop without `run_in_executor`. Evaluate `fastembed` or embeddings via Ollama in P2.
- **Machine 2 fallback** — Timeout + circuit breaker if Machine 2 is off. Not relevant in P1 (cloud LLM only).
- **Machine 1 VRAM under load** — Measure with `nvidia-smi` before planning Phase 2 SLMs.

### Technical constraints (not decisions, facts)

- **`aiosqlite` mandatory** — Synchronous SQLite in an asyncio event loop blocks everything. Non-negotiable.
- **No gRPC** — `httpx` HTTP/2 is sufficient for inter-machine throughput at personal use scale.
- **Machine 1 never shuts down** — hub, channels, database, TTS. Must be available 24/7.

---

## Phase 1 — Scope

What is built in Phase 1:
- Hub: asyncio bus (bounded queue) + bindings + pools + adapter registry
- Memory levels 0 (working) + 3 (semantic) only
- Telegram adapter (migration from 2ndBrain)
- Cloud LLM (Anthropic) as the sole generation engine

What is **explicitly excluded from Phase 1**:
- Memory levels 1 (session), 2 (episodic), 4 (procedural) — added when the real need arises
- Atomic SLMs (see Phase 2)
- Cognitive meta-language between SLMs
- Knowledge graph (optional level 4)
- Machine 2 / local LLM (added in P2 once the hub is stable)
- Hash-chained audit trail (P3 — unnecessary for personal use)
- Heavy `sentence-transformers` — evaluate `fastembed` or embeddings via Ollama in P2

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

## Next Steps (Phase 1)

1. Prototype the hub (bus + bindings + pools) — ~150 lines
2. Connect the existing Telegram adapter
3. Validate the hybrid memory layer (levels 2 + 3)
4. Migrate existing skills (2ndBrain) to the new system

---

## Business Projects

### Selected Tracks

1. **Automated YouTube** — script → voice (voicecli) → video (MoviePy/Remotion) → publication. French-speaking dev/AI niche. Revenue: AdSense + partnerships.
2. **Micro-SaaS** — roxabi_boilerplate base (Bun + TurboRepo + TanStack Start + NestJS). Local inference on Machine 2 = privacy selling point.
3. **Social media** — coordinated animation of roxabi_site + projects (Lyra, SaaS). Build in public.

**Synergies**: YouTube → clips recycled as posts → traffic to SaaS → funds the content.

---

### Priority SaaS: LegalTech for Lawyers/Notaries

**Context**: Real-world experience with Angelique (asset calculation for separation) + compte_appart → validated domain knowledge, existing real client.

**Market**: ~70,000 lawyers in France, poorly digitized, high perceived value (billed at 200-400EUR/h → 99-299EUR/month is a no-brainer).

**Key argument**: local LLM inference on Machine 2 → sensitive data never leaves the firm.

**Target features**:
- Personal injury damage calculation (IPP, AIPP, loss of earnings, third-party assistance)
- Divorce calculation: compensatory allowance, alimony, asset division
- Document generation: pleadings, summons, briefs from templates
- Opposing party document analysis: summary, weak points, counter-arguments
- Automated case timeline
- Moral/economic damage justification with case law

**Stack**: roxabi_boilerplate (frontend + backend) + local LLM Machine 2 via internal API.
