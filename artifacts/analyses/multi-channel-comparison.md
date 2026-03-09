# Multi-Channel AI Agent Frameworks — Comparative Analysis

> Analyzed: 2026-03-09
> Context: Researching best design patterns for Lyra's asyncio event system and channel adapters
> Repos: Nanobot, OpenClaw, NanoClaw, OpenFang, MoChat

## TL;DR

| Framework | Lang | Channels | Architecture | Best for |
|-----------|------|----------|-------------|---------|
| **Nanobot** | Python/asyncio | 9+ | Two-queue MessageBus + per-session Task | Python-first, lean, research |
| **OpenClaw** | TypeScript | 25+ | WebSocket gateway + append-only sessions | Production, multi-agent, persistence |
| **NanoClaw** | TypeScript | 5 | SQLite polling + container isolation | Security, simplicity, crash recovery |
| **OpenFang** | Rust | 40 | Kernel EventBus + WASM sandbox | Autonomous agents, enterprise scale |
| **MoChat** | TypeScript | Adapter | Social layer on OpenClaw/Nanobot | Agent federation, Phase 5+ |

## Dimension-by-Dimension Comparison

### 1. Event Ingestion

| Framework | Pattern | Latency | Reliability |
|-----------|---------|---------|-------------|
| Nanobot | WebSocket persistent + IMAP polling fallback | Low (WS) | Medium (no crash recovery) |
| OpenClaw | WebSocket to local gateway | Very low | High (persistent binding) |
| NanoClaw | SQLite polling + dual cursors | Medium | Very high (cursor rollback) |
| OpenFang | Per-adapter (WS/webhook/polling) | Varies | High (WASM metering) |

**Winner for Lyra**: Nanobot's WebSocket pattern (already using aiogram + discord.py). Add NanoClaw's dual-cursor crash recovery for #67.

### 2. Concurrency Model

| Framework | Model | Cancellation | Timeout | Multi-user |
|-----------|-------|-------------|---------|------------|
| Nanobot | Per-session asyncio.Task | Per-session | Per-task | Parallel tasks |
| OpenClaw | Per-session streaming | Per-session | Per-tool | Parallel sessions |
| NanoClaw | Bounded container pool (3-5) | Container kill | Container TTL | Parallel containers |
| OpenFang | Kernel scheduler + WASM fuel | Budget exhaustion | Epoch metering | Budget per agent |
| **Lyra (current)** | Per-user asyncio.Lock | Lock release | None | Sequential per user |

**Gap**: Lyra's Lock-per-user is the weakest model. Nanobot/OpenClaw's per-session Task is the right upgrade → #112.

### 3. Message Normalization

| Framework | Unified type | Cross-channel | Metadata |
|-----------|-------------|---------------|---------|
| Nanobot | `InboundMessage` / `OutboundMessage` | Yes (via MessageBus) | Preserved |
| OpenClaw | Session envelope | Yes (via MCP tools) | Preserved |
| NanoClaw | SQLite row + JID | Limited | Partial |
| OpenFang | `ChannelMessage` | Yes (via Hands) | Full fidelity |
| **Lyra (current)** | `Message` dataclass | Yes | Per-platform context |

**Assessment**: Lyra's Message type is solid. Main gap is the single queue — split into inbound/outbound (#112).

### 4. Session Persistence / Crash Recovery

| Framework | Storage | Crash recovery | Pattern |
|-----------|---------|---------------|---------|
| Nanobot | In-memory | None | Volatile |
| OpenClaw | Append-only JSONL | Yes (replay) | Per-session file |
| NanoClaw | SQLite + dual cursors | Yes (rollback) | Global + per-group cursor |
| OpenFang | SQLite + vectors + auto-repair | Yes (7-phase) | Session healing |
| **Lyra (current)** | In-memory (Pool.history) | None | Volatile |

**Gap**: Lyra has no crash recovery. #67 should implement OpenClaw's append-only JSONL + NanoClaw's dual-cursor pattern.

### 5. Backpressure

| Framework | Signal to adapter | Drop policy | Retry |
|-----------|------------------|-------------|-------|
| Nanobot | None (queue grows) | None | None |
| OpenClaw | Streaming throttle | Tool timeout | None |
| NanoClaw | Capacity rejection | Queue to waitingGroups | Exponential backoff |
| OpenFang | Budget exhaustion | Agent suspension | Scheduler retry |
| **Lyra (current)** | None (queue bound=100) | Silent overflow | None |

**Gap**: Lyra needs explicit backpressure signal + retry. NanoClaw's `MAX_CONCURRENT` + exponential backoff is the simplest model to adopt.

### 6. Plugin / Adapter Registration

| Framework | Pattern | Discovery | Hot-reload |
|-----------|---------|-----------|------------|
| Nanobot | Direct import + Task | Static | No |
| OpenClaw | Binding config | Static | No |
| NanoClaw | `register_channel(name, factory_fn)` at startup | Dynamic scan | No |
| OpenFang | Wave-based adapter registry | Static | No |
| **Lyra (current)** | Hardcoded SKILL_REGISTRY dict | Static | No |

**Gap**: Adopt NanoClaw's self-registration pattern for #106 (plugin system MVP).

### 7. Memory / State

| Framework | Storage | Search | Scope |
|-----------|---------|--------|-------|
| Nanobot | `~/.nanobot/` local files | None | Per-session |
| OpenClaw | Pluggable backends (SQLite, Redis, vector) | Pluggable | Per-agent |
| NanoClaw | Per-group CLAUDE.md files | None | Per-group |
| OpenFang | SQLite + vector embeddings | FTS + semantic | Per-agent |
| **Lyra (current/planned)** | roxabi-memory (SQLite + FTS5 + vectors) | Hybrid BM25+semantic | Per-namespace |

**Assessment**: Lyra's planned memory system (#83) is best-in-class among these. OpenFang's session healing (7-phase validation + auto-repair) is worth studying for #83's compaction logic.

## Patterns Worth Adopting (Prioritized)

### P0 — Immediate (maps to existing issues)

| Pattern | Source | Issue |
|---------|--------|-------|
| Split inbound/outbound queues | Nanobot | #112 |
| Per-session asyncio.Task instead of Lock | Nanobot + OpenClaw | #112 |
| Append-only JSONL session storage | OpenClaw | #67 |
| Dual-cursor crash recovery | NanoClaw | #67 |
| Self-registering plugin factory | NanoClaw | #106 |
| Exponential backoff + retry | NanoClaw | Future |

### P1 — Phase 1b

| Pattern | Source | Issue |
|---------|--------|-------|
| Persistent bindings across restarts | OpenClaw | #83 + #67 |
| Sub-worker config inheritance | OpenClaw | #99 (hub command sessions) |
| Backpressure signal to adapters | NanoClaw | #112 follow-up |
| Per-session Task cancellation (/stop) | Nanobot | #112 |

### P2 — Phase 2+

| Pattern | Source | Future |
|---------|--------|--------|
| Session healing + auto-repair | OpenFang | Post #83 |
| WASM sandbox for plugins | OpenFang | #106 full |
| Resource budget per agent | OpenFang | Multi-tenant |
| Agent federation (MoChat) | MoChat | Phase 5 (#63) |

## Relevance Ranking for Lyra

1. **Nanobot** — Best Python-first match. Two-queue MessageBus + per-session Task directly applicable now.
2. **OpenClaw** — Architectural twin. Persistent sessions, sub-worker inheritance, pluggable memory.
3. **NanoClaw** — Best crash recovery pattern. Dual-cursor + exponential backoff + self-registering plugins.
4. **OpenFang** — Best storage/search patterns. Session healing relevant for memory compaction.
5. **MoChat** — Agent federation layer. Phase 5+ only.

## What Lyra Already Does Right

- RoutingKey `(platform, bot_id, user_id)` — solid, matches OpenClaw's session key pattern
- asyncio-first with single event loop — correct foundation
- Protocol-based adapters (ChannelAdapter Protocol) — matches all frameworks' approach
- WebSocket via aiogram + discord.py — best-practice ingestion
- roxabi-memory (SQLite + FTS5 + vectors) — best-in-class memory among all analyzed
- Bounded queue (100) — better than Nanobot's unbounded

## What Lyra Is Missing

- Per-session Task (has per-user Lock instead)
- Split inbound/outbound queues
- Crash recovery (no cursor, no JSONL persistence)
- Explicit backpressure signal to adapters
- Dynamic plugin registration
- Exponential backoff on LLM failure
