# ScalyClaw — Deep Analysis

> **URL**: https://github.com/scalyclaw/scalyclaw
> **Date**: 2026-03-10
> **Category**: Self-hosted AI assistant platform — all-in-one, horizontally scalable
> **Family**: ClawFamily (complete product, not a framework)
> **License**: MIT

---

## TL;DR

ScalyClaw is the **most complete, production-oriented project in the ClawFamily**. Where OpenClaw is an agent framework, Paperclip an orchestration layer, and MetaClaw a learning proxy — ScalyClaw is a **finished product**: self-hosted, multi-channel AI assistant with proactive engagement, 4-layer security, horizontal worker scaling, and a full web dashboard.

**Tagline**: *"The AI That Scales With You. One mind · All channels · Continuous relationship."*

---

## 1. Product Overview

### What It Is

A self-hosted AI assistant platform that connects to 7 messaging channels (Discord, Telegram, Slack, WhatsApp, Signal, Teams, Web) under a single shared memory. It runs code via isolated workers, delegates to sub-agents, proactively initiates conversations based on signal detection, and monitors itself through a React dashboard.

### Core Problem Solved

Most AI assistants are channel-specific (one Telegram bot, one Discord bot). ScalyClaw gives one persistent identity across all channels — same memory, same personality, same relationship continuity. And unlike purely reactive assistants, it can initiate conversations when it detects the right moment.

### What Makes It Different

| vs. | ScalyClaw does |
|-----|----------------|
| Single-channel bots | 7 channels, shared memory across all |
| Reactive-only assistants | Proactive engine — AI initiates based on signals |
| Framework-only projects (OpenClaw) | Complete product with dashboard, CLI, install script |
| No security model | 4-layer guard system, all fail-closed |
| Single-process monoliths | Singleton node + horizontally scalable workers (Redis only shared dep) |
| No budget control | Per-model token tracking, daily/monthly limits |

---

## 2. Tech Stack

| Layer | Technology |
|-------|-----------|
| **Runtime** | Bun |
| **Queue** | BullMQ + Redis |
| **Database** | SQLite + sqlite-vec + FTS5 |
| **LLM** | Any OpenAI-compatible API |
| **Channels** | Telegraf, discord.js, @slack/bolt, botbuilder, WhatsApp Cloud API, Signal REST |
| **MCP** | @modelcontextprotocol/sdk (stdio, HTTP, SSE) |
| **HTTP** | Fastify |
| **Dashboard** | React 19, Vite 6, Tailwind CSS 4, shadcn/ui |
| **CLI** | Commander + @clack/prompts |
| **Language** | TypeScript (100%) |
| **Version** | 0.1.0 |

### Skill Languages

| Language | Runtime |
|----------|---------|
| JavaScript | `bun run` |
| Python | `uv run` |
| Rust | `cargo run --release` |
| Bash | `bash` |

Dependencies auto-install on first run. Hot-reload via Redis pub/sub. Zip deployment.

---

## 3. Architecture

### Process Model

```
┌─────────────────────────────────────────────────────────────┐
│                      SCALYCLAW NODE                          │
│  (singleton)                                                  │
│  ┌──────────┐  ┌──────────┐  ┌────────┐  ┌──────────────┐  │
│  │ Channels │  │ Guards   │  │ Memory │  │  Proactive   │  │
│  │ (7)      │  │ (4 layers│  │ SQLite │  │  Engine      │  │
│  └────┬─────┘  └──────────┘  └────────┘  └──────────────┘  │
│       │ BullMQ jobs                                          │
└───────┼─────────────────────────────────────────────────────┘
        │
┌───────▼────────────────────────────────────────────────────┐
│                       REDIS                                  │
│  Queue · Pub/Sub · Vault · Activity tracking · Proactive    │
└───────┬────────────────────────────────────────────────────┘
        │
┌───────▼────────────────────────────────────────────────────┐
│                   WORKERS (N instances)                      │
│  (horizontally scalable — only need Redis)                   │
│  ┌──────────────────────────────────────────────────────┐   │
│  │  Skill execution · Code · Commands · Agent runners   │   │
│  └──────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────┘
        │
┌───────▼────────────────────────────────────────────────────┐
│                    DASHBOARD (React SPA)                     │
│  Real-time monitoring · Config · Chat overlay · Jobs        │
└────────────────────────────────────────────────────────────┘
```

### Message Processing Pipeline

Every incoming message goes through:

```
Channel → BullMQ queue
→ Message Guard (echo + content, parallel)
→ AbortController registration
→ Orchestrator (LLM loop)
→ Response Echo Guard
→ Store message
→ Debounced memory extraction (Redis buffer, BullMQ delay)
→ Publish progress to channel
```

### BullMQ Queues

| Queue | Jobs |
|-------|------|
| `message` | message-processing, command |
| `agent` | agent-processing |
| `internal` | memory-extraction, proactive-eval |

Workers are named instances (`--name worker1`). Redis is the only shared dependency — no shared filesystem.

### Zero-Downtime Reload

All of these reload live via Redis pub/sub without restart:
- Skills
- Agents
- Config (channels, models, guards)
- MCP servers
- Embeddings model

---

## 4. Key Systems Deep-Dive

### 4.1 Memory

Memory is stored in SQLite with three parallel indices:

| Index | Purpose |
|-------|---------|
| `memories` table | Main store (subject, content, type, tags, importance, TTL, access_count) |
| `memory_vec` | sqlite-vec for vector similarity search |
| `memory_fts` | FTS5 for full-text search fallback |
| `memory_tags` | Normalized tag index |

**Search strategy**: vector search first, FTS5 fallback if no results or embeddings unavailable.

**Composite scoring** (configurable weights):
```
composite = f(semantic_score, recency_decay, importance)
weights: { semantic: 0.6, recency: 0.2, importance: 0.2 }
```

Memory types: `fact`, `preference`, `event`, `relationship` — each with confidence score, TTL, consolidation pointer.

Entity extraction (`memory/entities.ts`) + consolidation (`memory/consolidation.ts`) are separate async passes.

Access tracking is non-blocking (`trackAccessBatch` via async queue).

### 4.2 Proactive Engagement Engine

**This is ScalyClaw's most unique feature in the family.**

Two-phase pipeline decoupled by BullMQ:

**Phase 1 — Signal Scan (cron, no LLM)**:
```
Detect signals (7 types, all deterministic) →
Aggregate into trigger type (idle/urgent/deliverable/follow_up/insight/check_in) →
Adaptive threshold check →
Timing check (quiet hours, user activity pattern) →
Enqueue proactive-eval job
```

**Signal types**:
- `idle` — channel inactive past threshold
- `time_sensitive` — upcoming deadline from memory
- `pending_deliverable` — unresolved commitment
- `unfinished_topic` — conversation thread not concluded
- `entity_trigger` — recurring entity in recent messages
- `user_pattern` — matches user's historical active times
- `return_from_absence` — user back after multi-day silence

**Phase 2 — Deep Evaluation (BullMQ job, uses LLM)**:
```
Re-detect signals (may have changed) →
Rate limit checks (cooldown + daily cap per trigger type) →
Assemble context (memory + conversation history) →
LLM eval: should engage? what type? confidence? →
LLM generate message →
Find best delivery channel →
Apply cooldown + daily counter →
Store + deliver
```

**Adaptive threshold**: adjusts based on engagement history (user response rate, sentiment classification).

**Sentiment heuristic** (no LLM): keyword matching + response length > 20 chars = positive.

### 4.3 Security — 4-Layer Guard System

All guards fail-closed by default (`failOpen` opt-in per guard).

| Guard | Mechanism | Runs |
|-------|-----------|------|
| **Echo Guard** | LLM asked to echo text → cosine similarity check. Low score = injection attempt | On every message |
| **Content Guard** | LLM security analysis → JSON `{safe, reason, threats}` | On every message |
| **Skill Guard** | LLM audits SKILL.md + script contents | On skill registration |
| **Agent Guard** | LLM audits agent definition + system prompt | On agent registration |
| **Command Shield** | Deterministic pattern matching (denied/allowed lists) — no LLM | On every command |

Echo + Content guards run **in parallel** when both enabled.

Model for guards is configurable independently — can use a cheaper/faster model.

### 4.4 Model Registry

- Multiple models per role (chat, guard, embedding)
- Priority + weight load balancing: lowest priority group tried first, within group weighted-random
- Auto-failover to next priority group if entire group fails
- OpenAI-compatible API — OpenAI, Anthropic, Ollama, LM Studio, MiniMax, any OpenAI-compatible endpoint
- Budget: monthly/daily limits, soft or hard enforcement, per-model token tracking

### 4.5 Skills & Agents

**Skills**: folder + `SKILL.md` manifest + script. Languages: JS (Bun), Python (uv), Rust, Bash. Auto-deps on first run. Hot-reload. Zip deployment. Guarded by LLM skill auditor.

**Agents**: sub-workers with own prompt, model, skill set, permissions. Delegated via BullMQ `agents` queue. Managed from dashboard. Guarded by LLM agent auditor.

---

## 5. Feature Matrix

| Feature | Status |
|---------|--------|
| 7 messaging channels | ✅ |
| Persistent hybrid memory | ✅ sqlite-vec + FTS5 |
| Auto memory extraction | ✅ debounced, background |
| Memory consolidation | ✅ |
| Entity tracking | ✅ |
| Proactive engagement engine | ✅ (unique in family) |
| Sub-agents | ✅ |
| Skills (4 languages) | ✅ |
| MCP integration | ✅ stdio/HTTP/SSE |
| 4-layer security | ✅ all fail-closed |
| Horizontal worker scaling | ✅ Redis-only dep |
| Zero-downtime reload | ✅ Redis pub/sub |
| Budget control | ✅ per-model |
| Web dashboard | ✅ React 19 + Vite 6 |
| Encrypted vault | ✅ Redis-backed |
| Priority + weight LB | ✅ |
| Adaptive proactive threshold | ✅ |

---

## 6. Business Model & Positioning

### Current
- **MIT licensed, fully self-hosted** — no SaaS, no paid tier, no token, no crypto
- No external training dependency (unlike MetaClaw → Tinker)
- Self-contained: one-line install script

### Positioning
- Personal AI assistant → small team platform
- Designed for technical users who want full control
- Competitors: Home Assistant (for IoT), n8n (for automation), custom bot stacks

### Not For
- Non-technical users (no hosted version, setup requires Redis + LLM API keys)
- LLM training / fine-tuning (inference only)
- Enterprise multi-tenant (no RBAC yet, no multi-user separation at node level)

---

## 7. GitHub Metrics (2026-03-10)

| Metric | Value |
|--------|-------|
| Stars | **10** |
| Forks | 2 |
| Language | TypeScript (100%) |
| Created | 2026-02-24 (~2 weeks ago) |
| Last updated | Active (recent commits: dashboard animations, git skills) |
| License | MIT |
| Version | 0.1.0 |
| Monorepo | bun workspaces (shared, scalyclaw, worker, cli, dashboard) |

Very early — 2 weeks old, pre-announcement phase. The README is polished and the feature set is complete, suggesting this is about to be properly launched.

---

## 8. Key Design Decisions

| Decision | Why It Matters |
|----------|---------------|
| **Workers share only Redis** | Deploy workers anywhere — same machine, remote server, cloud. Zero shared filesystem coupling. |
| **BullMQ for everything** | Durable jobs, retries, priorities, delay, backoff — all message processing is async and resilient. |
| **Debounced memory extraction** | Buffers messages in Redis list, extracts in background after silence. No latency hit per message. |
| **2-phase proactive (cron + queue)** | Phase 1 is free (no LLM). Phase 2 only runs if signals warrant it. Budget-aware by design. |
| **Guards in parallel** | Echo + Content guards run together — no sequential latency stacking. |
| **Fail-closed default** | Security conservative: if guard errors, block. Opt-in `failOpen` available. |
| **Composite memory scoring** | Not just semantic similarity — recency decay + importance weighted in. Better retrieval quality. |
| **Skills as folders** | Human-readable, version-controllable, hot-reloadable, deployable as zips. Same philosophy as Claude Code skills. |
| **OpenAI-compatible only** | Broad provider support out of the box — Anthropic, OpenAI, Ollama, LM Studio, any compatible endpoint. |

---

## 9. ClawFamily Comparison

### Position in the Ecosystem

```
┌─────────────────────────────────────────────────────────────┐
│                      PAPERCLIP                               │
│  Control-plane: org charts, goals, budgets, multi-agent      │
└──────────────────┬──────────────────────────────────────────┘
                   │ orchestrates
┌──────────────────▼──────────────────────────────────────────┐
│                      OPENCLAW                                │
│  The agent — executes tasks, uses tools, single-channel      │
└──────────────────┬──────────────────────────────────────────┘
                   │ proxied by (optional)
┌──────────────────▼──────────────────────────────────────────┐
│                      METACLAW                                │
│  Learning-plane: intercepts, scores, trains LoRA             │
└─────────────────────────────────────────────────────────────┘

         ╔══════════════════════════════════════════╗
         ║              SCALYCLAW                   ║
         ║  Complete product: 7 channels + workers  ║
         ║  + proactive + dashboard + security       ║
         ║  (parallel track, not layered on others)  ║
         ╚══════════════════════════════════════════╝
```

ScalyClaw is **not layered on OpenClaw** — it's a parallel complete implementation. It solves the same base problem as OpenClaw (AI assistant) but as a finished product rather than a framework.

### Head-to-Head

| Dimension | OpenClaw | IronClaw | NanoClaw | ScalyClaw |
|-----------|---------|---------|---------|----------|
| **Type** | Framework | Prod-hardened | Lightweight | Complete product |
| **Channels** | Multi (framework) | Multi | 1-2 | 7 |
| **Proactive** | ❌ | ? | ❌ | ✅ |
| **Dashboard** | ❌ | ? | ❌ | ✅ |
| **Worker scaling** | ❌ | ? | ❌ | ✅ horizontal |
| **Security guards** | Basic | ? | ❌ | ✅ 4-layer |
| **Memory** | RAG-based | ? | SQLite | SQLite + vec + FTS5 |
| **Install** | npm setup | ? | npm | one-line curl |
| **Language** | TypeScript | TypeScript | TypeScript | TypeScript (Bun) |

---

## 10. Relevance to Lyra / 2ndBrain

### Direct Alignments

| ScalyClaw concept | Lyra equivalent | Notes |
|------------------|----------------|-------|
| Proactive engine (2-phase, signal → LLM eval) | Not implemented yet | **Most valuable borrow.** Lyra has no proactive layer. ScalyClaw's architecture (cron scan → BullMQ job) maps cleanly to Lyra's asyncio.Queue bus. |
| Composite memory score (semantic + recency + importance) | BM25 + embeddings (2ndBrain) | Lyra should add recency decay and importance weights to its memory scoring. |
| Memory entity extraction (separate pass) | Not implemented | Useful for Lyra's semantic memory level. |
| Guard architecture (fail-closed, configurable per guard) | Not implemented | Lyra lacks security layers entirely. Echo guard + command shield are both simple to implement. |
| Worker isolation (Redis-only dep) | asyncio.Queue | Same decoupling philosophy. Lyra could export "worker" as a separate process if needed. |
| Debounced memory extraction (buffer + delay) | Not implemented | Prevents per-message embedding cost. Good pattern. |
| Adaptive engagement threshold | Not implemented | Learning from user response patterns to tune when to initiate — directly applicable to Lyra. |
| Hot-reload via pub/sub | Not implemented | Lyra's reload mechanism is manual restart. Redis pub/sub (or asyncio Event) is the clean solution. |

### What Lyra Can't Directly Use

| ScalyClaw feature | Why |
|------------------|-----|
| BullMQ | Node.js/Bun. Lyra is Python — use asyncio.Queue + celery/arq if needed. |
| Dashboard (React) | Lyra has no frontend target yet. Could be inspiration for future admin UI. |
| sqlite-vec | Lyra already uses it (2ndBrain). Direct port possible. |

### Key Borrow: Proactive Engine Architecture

The 2-phase pattern (cheap cron scan → expensive LLM eval) is the exact right architecture for Lyra's proactive layer:

```
# Lyra adaptation (Python asyncio)
async def proactive_scan_loop():
    """Cron-like loop. No LLM. Deterministic."""
    while True:
        signals = detect_signals()  # idle time, pending topics, entities
        trigger = aggregate(signals, weights)
        if trigger and above_adaptive_threshold(trigger):
            await queue.put(ProactiveEvalTask(signals))
        await asyncio.sleep(scan_interval)

async def proactive_eval_worker():
    """Queue consumer. Uses LLM."""
    while True:
        task = await queue.get()
        if not rate_limit_ok():
            continue
        context = await assemble_context(task.signals)
        if await llm_should_engage(context):
            message = await llm_generate(context)
            await deliver(best_channel(), message)
```

### Key Borrow: Guard Architecture

Two guards are immediately useful for Lyra with zero LLM cost:
1. **Command shield**: deterministic pattern matching for dangerous shell commands
2. **Similarity-based echo guard**: detect injection attempts via cosine similarity (no LLM needed for the check itself)

---

## 11. Risks & Concerns

| Risk | Severity | Notes |
|------|----------|-------|
| Very early (0.1.0, 2 weeks) | High | No production track record. APIs will change. |
| Low community (10 stars) | High | Small userbase means fewer bug reports, less battle-testing. |
| Redis hard dependency | Medium | Every process needs Redis. No Redis = nothing works. Single point of failure if Redis goes down. |
| Guards use LLM = latency + cost | Medium | Echo + content guards run on every message. At scale this adds significant latency and token cost. |
| No multi-user RBAC | Medium | Single-user or trusted-group only. No user separation at the node level. |
| Bun runtime | Low | Bun is stable but less battle-tested than Node. Some npm packages have Bun compatibility issues. |
| No fine-tuning / learning | Low | Unlike MetaClaw, ScalyClaw doesn't improve its underlying model. Skills/agents evolve, weights don't. |

---

## Summary

ScalyClaw is the **most complete product in the ClawFamily** — the only one shipping a self-contained, horizontally scalable, multi-channel AI assistant with proactive engagement, security guards, a web dashboard, and a one-line install.

Its most novel contribution is the **2-phase proactive engine**: cheap deterministic signal scan → expensive LLM evaluation only when warranted. This is a production-quality pattern that solves the "AI assistant as passive tool" problem.

The memory system is also the most sophisticated in the family: composite scoring (semantic + recency + importance), entity extraction, TTL, consolidation — not just "store and retrieve."

Too early for production use (0.1.0, 10 stars), but the architecture is clean and the patterns are directly borrowable.

**Top 3 borrows for Lyra**:
1. **Proactive engine** — 2-phase cron+queue pattern
2. **Composite memory scoring** — add recency decay + importance to existing BM25+vec
3. **Guard architecture** — fail-closed, configurable, parallel execution
