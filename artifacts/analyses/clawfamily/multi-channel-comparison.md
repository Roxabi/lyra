# Multi-Channel AI Agent Frameworks — Comparative Analysis

> Analyzed: 2026-03-09
> Context: Researching best design patterns for Lyra's asyncio event system and channel adapters
> Repos: Nanobot, OpenClaw, NanoClaw, OpenFang, MoChat, IronClaw

## Maturity Leaders by Category

| Category | Leader | Score |
|----------|--------|-------|
| Memory (storage + search) | OpenClaw + IronClaw | 5/5 |
| Context Management | OpenClaw | 5/5 |
| Channel Count | OpenFang (40 adapters) | 5/5 |
| Channel Abstraction | OpenClaw (7-tier bindings) | 5/5 |
| Bus / Events | OpenClaw + OpenFang | 5/5 |
| LLM Providers | IronClaw (decorator chain) | 5/5 |
| Tool System | IronClaw + OpenFang | 5/5 |
| Plugin System | OpenClaw (typed SDK) | 5/5 |
| Security | OpenFang (16 layers) + IronClaw | 5/5 |
| Orchestration | OpenFang (workflow engine) | 5/5 |
| Observability | OpenClaw (diagnostic events) | 5/5 |
| Readability | Nanobot + Lyra | 5/5 |

## TL;DR

| Framework | Lang | Channels | Architecture | Best for |
|-----------|------|----------|-------------|---------|
| **Nanobot** | Python/asyncio | 9+ | Two-queue MessageBus + per-session Task | Python-first, lean, research |
| **OpenClaw** | TypeScript | 25+ | WebSocket gateway + append-only sessions | Production, multi-agent, persistence |
| **NanoClaw** | TypeScript | 5 | SQLite polling + container isolation | Security, simplicity, crash recovery |
| **OpenFang** | Rust | 40 | Kernel EventBus + WASM sandbox | Autonomous agents, enterprise scale |
| **IronClaw** | Rust/Tokio | 5+ | Decorator chain + WASM tools + credential proxy | LLM resilience, security, tool safety |
| **MoChat** | TypeScript | Adapter | Social layer on OpenClaw/Nanobot | Agent federation, Phase 5+ |

## Dimension-by-Dimension Comparison

### 1. Event Ingestion

| Framework | Pattern | Latency | Reliability |
|-----------|---------|---------|-------------|
| Nanobot | WebSocket persistent + IMAP polling fallback | Low | Medium (no crash recovery) |
| OpenClaw | WebSocket to local gateway | Very low | High (persistent binding) |
| NanoClaw | SQLite polling + dual cursors | Medium | Very high (cursor rollback) |
| OpenFang | Per-adapter (WS/webhook/polling) | Varies | High (WASM metering) |
| IronClaw | `select_all()` over channel streams | Very low | High (Tokio work-stealing) |
| **Lyra (current)** | aiogram WS + discord.py WS | Low | Medium (no crash recovery) |

**Best for Lyra**: Current approach is correct. Add NanoClaw's dual-cursor crash recovery for #67.

### 2. Concurrency Model

| Framework | Model | Cancellation | Timeout | Multi-user |
|-----------|-------|-------------|---------|------------|
| Nanobot | Per-session asyncio.Task | Per-session | Per-task | Parallel tasks |
| OpenClaw | Per-session streaming | Per-session | Per-tool | Parallel sessions |
| NanoClaw | Bounded container pool (3-5) | Container kill | Container TTL | Parallel containers |
| OpenFang | Kernel scheduler + WASM fuel | Budget exhaustion | Epoch metering | Budget per agent |
| IronClaw | tokio::spawn per job + `max_parallel_jobs` | CancellationToken | Per-decorator | Parallel jobs |
| **Lyra (current)** | Per-user asyncio.Lock | Lock release | None | Sequential per user |

**Gap**: Lyra's Lock-per-user is the weakest. Target: Nanobot/OpenClaw per-session Task → #112.

### 3. LLM Resilience

| Framework | Retry | Circuit Breaker | Cache | Failover | Routing |
|-----------|-------|----------------|-------|---------|---------|
| Nanobot | None | None | None | None | None |
| OpenClaw | Basic | None | None | None | None |
| NanoClaw | Exponential backoff | None | None | None | None |
| OpenFang | Yes | Yes | None | None | None |
| **IronClaw** | ✅ Exp+jitter | ✅ 3-state | ✅ SHA-256 LRU | ✅ Cooldown | ✅ cheap/complex |
| **Lyra (current)** | None | None | None | None | None |

**IronClaw wins decisively**. The composable decorator chain is the model for #104 (circuit breaker).

### 4. Session Persistence / Crash Recovery

| Framework | Storage | Crash recovery | Pattern |
|-----------|---------|---------------|---------|
| Nanobot | In-memory | None | Volatile |
| OpenClaw | Append-only JSONL | Yes (replay) | Per-session file |
| NanoClaw | SQLite + dual cursors | Yes (rollback) | Global + per-group cursor |
| OpenFang | SQLite + vectors + auto-repair | Yes (7-phase) | Session healing |
| IronClaw | PostgreSQL per-session Thread | Yes (DB-backed) | Full persistence |
| **Lyra (current)** | In-memory (Pool.history) | None | Volatile |

**Gap**: #67 should combine OpenClaw's append-only JSONL + NanoClaw's dual-cursor rollback.

### 5. Memory / Search

| Framework | Storage | FTS | Vector | Fusion |
|-----------|---------|-----|--------|--------|
| Nanobot | Local files | None | None | None |
| OpenClaw | Pluggable backends | Yes | Yes | Pluggable |
| NanoClaw | Per-group CLAUDE.md | None | None | None |
| OpenFang | SQLite + pgvector | FTS5 | sqlite-vec | None |
| **IronClaw** | PostgreSQL + pgvector | ts_rank_cd | pgvector | **RRF fusion** |
| **Lyra (planned)** | roxabi-memory SQLite | FTS5/BM25 | fastembed ONNX | Hybrid |

**IronClaw's RRF** (Reciprocal Rank Fusion) is the right algorithm for combining FTS + vector scores. Lyra's #82 hybrid search should use RRF, not naive score averaging.

### 6. Security

| Framework | Sandbox | Credential Proxy | Injection Defense | Rate Limit |
|-----------|---------|-----------------|-------------------|------------|
| Nanobot | None | None | None | None |
| OpenClaw | Session policies | None | None | None |
| NanoClaw | OS containers | None | None | None |
| OpenFang | WASM (16 layers) | None | Prompt injection scanner | GCRA per-IP |
| **IronClaw** | WASM + allowlist | ✅ Agents never see keys | ✅ 7 layers | Token bucket per-tool |
| **Lyra (current)** | None | None | None | None |

**IronClaw's credential proxy** is the model for #106 (plugin system): plugins declare needed secrets by name, hub injects values at boundary.

### 7. Tool / Plugin System

| Framework | Registration | Approval | Discovery | Hot-reload |
|-----------|-------------|---------|-----------|------------|
| Nanobot | Direct import | None | Static | No |
| OpenClaw | Binding config | None | Static | No |
| NanoClaw | `register_channel()` factory | None | Dynamic scan | No |
| OpenFang | Wave-based registry | WASM capabilities | Static | No |
| **IronClaw** | WASM `.wasm` files + MCP | **3-tier approval** | `~/.ironclaw/tools/` scan | No |
| **Lyra (current)** | Hardcoded dict | None | Static | No |

**IronClaw's approval model** (`Never/UnlessAutoApproved/Always`) is essential for #106. Adopt from day one.

### 8. Backpressure

| Framework | Signal to adapter | Drop policy | Retry |
|-----------|------------------|-------------|-------|
| Nanobot | None (queue grows) | None | None |
| OpenClaw | Streaming throttle | Tool timeout | None |
| NanoClaw | Capacity rejection | waitingGroups queue | Exponential backoff |
| OpenFang | Budget exhaustion | Agent suspension | Scheduler retry |
| IronClaw | `max_parallel_jobs` | Job queuing | Per-decorator |
| **Lyra (current)** | None (queue bound=100) | Silent overflow | None |

## The "Chimera" Strategy — Best of Each for Lyra

> ~1,160 lines of new Python gives Lyra capabilities that took 4K–137K lines elsewhere.
> Adopt the interfaces and patterns, not the implementations.

### Phase 1b (~550 LOC)

| # | Pattern | Source | Issue | Description |
|---|---------|--------|-------|-------------|
| 1 | Provider Registry | Nanobot | #76 follow-up | Metadata-driven auto-detection via JSON/TOML, zero-code additions for new LLM backends |
| 2 | LLM Decorator Chain | **IronClaw** | #104 | Retry + SmartRouting + Failover + CircuitBreaker + Cache as composable wrappers |
| 3 | Hybrid RRF Search | OpenClaw + **IronClaw** | #83 | FTS always works, vector boosts when available, RRF fusion (not naive averaging) |
| 4 | ContextEngine Protocol | OpenClaw | #83 | Pluggable token-budget assembly — fetch only what fits in context window |

### Phase 2 (~610 LOC)

| # | Pattern | Source | Issue | Description |
|---|---------|--------|-------|-------------|
| 5 | Lane-Based Queue | OpenClaw | #112 | Cron/background tasks in separate lane — don't block user message processing |
| 6 | Binding Resolution Tiers | OpenClaw | #112 | Graduated routing: exact match → wildcard → default agent |
| 7 | Tool Approval Levels | **IronClaw** | #106 | `Never/UnlessAutoApproved/Always` — mandatory for plugin safety |
| 8 | Credential Proxy | **IronClaw** + NanoClaw | #106 | Plugins declare secret names, hub injects values at boundary |
| 9 | Diagnostic Events | OpenClaw | #44 | Stuck detection, token tracking, per-session observability |
| 10 | Prompt Injection Scanner | OpenFang | Future | Pattern-based with severity levels (Block/Warn/Review/Sanitize) |

## Patterns Worth Adopting (Prioritized)

### P0 — Immediate (maps to existing issues)

| Pattern | Source | Issue |
|---------|--------|-------|
| Split inbound/outbound queues | Nanobot | #112 |
| Per-session asyncio.Task instead of Lock | Nanobot + OpenClaw | #112 |
| Append-only JSONL session storage | OpenClaw | #67 |
| Dual-cursor crash recovery | NanoClaw | #67 |
| Self-registering plugin factory | NanoClaw | #106 |
| Tool approval levels (Never/UnlessAutoApproved/Always) | **IronClaw** | #106 |
| LLM decorator chain (retry + circuit breaker + cache) | **IronClaw** | #104 |

### P1 — Phase 1b

| Pattern | Source | Issue |
|---------|--------|-------|
| RRF fusion for hybrid search | **IronClaw** | #83 |
| Credential proxy at hub boundary | **IronClaw** | #106 |
| Persistent bindings across restarts | OpenClaw | #83 + #67 |
| Sub-worker config inheritance | OpenClaw | #99 |
| Backpressure signal to adapters | NanoClaw | #112 follow-up |
| Exponential backoff with jitter | IronClaw | #104 |

### P2 — Phase 2+

| Pattern | Source | Future |
|---------|--------|--------|
| Lane-based queue (background vs user) | OpenClaw | #112 follow-up |
| Diagnostic events + stuck detection | OpenClaw | #44 |
| Session healing + auto-repair | OpenFang | Post #83 |
| WASM sandbox for plugins | IronClaw + OpenFang | #106 full |
| Prompt injection scanner | OpenFang | Security epic |
| Resource budget per agent | OpenFang | Multi-tenant |
| Agent federation (MoChat) | MoChat | Phase 5 (#63) |

## Relevance Ranking for Lyra

1. **IronClaw** — Best LLM resilience (decorator chain), tool safety (approval levels), credential proxy, RRF search. High relevance now.
2. **Nanobot** — Best Python-first match. Two-queue MessageBus + per-session Task directly applicable now.
3. **OpenClaw** — Architectural twin. Persistent sessions, sub-worker inheritance, pluggable memory, diagnostic events.
4. **NanoClaw** — Best crash recovery. Dual-cursor + exponential backoff + self-registering plugins.
5. **OpenFang** — Best channel breadth + autonomous scheduling. Session healing for memory compaction reference.
6. **MoChat** — Agent federation layer. Phase 5+ only.

## What Lyra Already Does Right

- RoutingKey `(platform, bot_id, user_id)` — solid, matches OpenClaw's session key pattern
- asyncio-first with single event loop — correct foundation
- Protocol-based adapters (ChannelAdapter Protocol) — matches all frameworks' approach
- WebSocket via aiogram + discord.py — best-practice ingestion
- roxabi-memory (SQLite + FTS5 + vectors) — competitive with IronClaw/OpenClaw
- Bounded queue (100) — better than Nanobot's unbounded

## What Lyra Is Missing

- Per-session Task (has per-user Lock instead) → #112
- Split inbound/outbound queues → #112
- Crash recovery (no cursor, no JSONL persistence) → #67
- LLM decorator chain (no retry/circuit-breaker/cache) → #104
- Tool approval levels → #106
- Credential proxy for plugins → #106
- RRF fusion in hybrid search → #83
- Explicit backpressure signal to adapters → future
- Diagnostic events / stuck detection → #44
