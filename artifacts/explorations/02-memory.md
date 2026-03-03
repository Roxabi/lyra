# Memory — 5-Level Architecture

> Lyra status: **Architecturally decided, not implemented**
> Last updated: 2026-03-02

---

## Where we stand

5-level memory architecture inspired by human cognition:

| Level | Duration | Storage | Example |
|-------|----------|---------|---------|
| **Working** | < 1 turn | Context window | "This current message" |
| **Session** | Session duration | Append-only log | "What we said tonight" |
| **Episodic** | Weeks/months | Dated Markdown | "March 2 conversation about Lyra" |
| **Semantic** | Permanent | BM25 + sqlite-vec | "Mickael prefers Python" |
| **Procedural** | Permanent | Markdown | "How to generate a voice note" |

**Consolidation**: working -> session -> episodic -> semantic. With time-decay on episodic.

**Hard rule**: `aiosqlite` mandatory from the start. No synchronous SQLite.

---

## What we have in the knowledge base

### Agentic Note-Taking 19: Living Memory (Cornelius)
> 3 memory systems inspired by Tulving

1. **Semantic** (knowledge graph) — general, durable facts
2. **Episodic** (self space) — dated, contextualized experiences
3. **Procedural** (methodology) — know-how, workflows

Each space has a **different metabolic rate**: semantic slow (stable facts), episodic medium (events), procedural slow (methods).

**The flows between them are directional** — like a digestive system: raw info -> knowledge.

> Confirms our architecture. The "metabolic rate" concept is powerful for deciding what to consolidate and when.

### Reducing token costs by 67% (Polymarket agent)
> 8,200 -> 2,700 tokens/request (-67%), $73 -> $24/day

Two-level system: critical bootstrap (always loaded) + MEMORY.md with **semantic search** (loaded on demand based on the request).

**Key insight**: do not load everything into context. Retrieve what is relevant for *this* request.

> Our semantic level must be queryable, not load-everything.

### Memory as Reasoning (Plastic Labs)
> Treating memory as a dynamic reasoning task

LLMs can excel at logical reasoning (deduction, induction, abduction) to create **composable and updatable identity representations**, surpassing vector databases.

Instead of "what is the closest memory?", ask "what is true about this user given these observations?"

> Our Phase 2 semantic level could use a memory SLM to perform this synthesis.

### Fix OpenClaw memory: 3 failure modes
> Comprehensive guide on recurring problems

1. **Memory never saved** — the model decides on its own -> our architecture forces explicit writes
2. **Saved but never retrieved** — the agent answers from context instead of searching -> mandatory retrieval at session start
3. **Destroyed by compaction** — context is compacted and memory disappears -> **mandatory external memory flush before compaction**

> Hard rule to implement: before any compaction, flush to episodic memory.

### Performance boost: semantic memory in PreToolUse hook
> Zac, drastically better performance

Including semantic memory in **PreToolUse** hooks (not just UserPromptSubmit) boosts agent effectiveness.

> Our hub must inject relevant memory before each tool call, not just at the start of a message.

### ClawVault v2.6: Malleable YAML Primitives
> Long-term agent autonomy

Composable primitives: tasks, projects, decisions, lessons, people — markdown files with YAML frontmatter.

**Key**: the agent can **modify its own schemas** over time. Templates evolve.

> Our procedural memory should have this evolutionary character: Lyra can modify its own procedures.

### The Three-Layer Memory System (Clawdbot upgrade)
> Self-maintaining, compounding knowledge graph

Migration from static files to:
1. **Automatic fact extraction** at each session end
2. **Knowledge graph** with relationships between entities
3. **Automatic weekly synthesis**

> Our consolidation should be automated, not manual.

### Memory optimization (QMD + Qdrant, re-indexing crashes)
> Force re-indexing every 30min = cascade of rate limits

**Lesson**: indexing is like backups — space them at least 6h apart. Never trigger like a heartbeat.

> Our consolidation scheduler: nightly batch, not real-time.

---

## Challenges and open questions

### 1. What triggers consolidation?

Options:
- **End of session** -> always consolidate working+session -> episodic
- **Time threshold** -> semantic consolidation every N hours (nightly batch)
- **Quantity threshold** -> consolidate when N episodes in the queue
- **Quality** -> consolidate only if the memory SLM judges it as new/important

**Recommendation**: end of session + nightly batch. No real-time.

### 2. Time-decay on episodic — how?

Options:
- **Soft decay**: each episode has a score that decreases. Below a threshold -> archived
- **Hard TTL**: episodes > 90 days -> purged unless referenced
- **Importance-weighted**: the user can "pin" important episodes

### 3. Episodic / semantic boundary

When does information become "semantic"?
- "Mickael said he prefers Python" (episodic once)
- "Mickael prefers Python" (semantic after N confirmations)

> Rule: after 3 concordant mentions -> promotion to semantic. Memory SLM to decide.

### 4. Cross-channel memory

If Mickael talks to Lyra via Telegram AND Discord, do memories share or isolate?
- By default: **shared** (same canonical user_id)
- But: is this always desirable?

### 5. SQLite schema

Minimal proposal:
```sql
-- Episodic
CREATE TABLE episodes (
    id TEXT PRIMARY KEY,
    user_id TEXT,
    channel TEXT,
    timestamp_start DATETIME,
    timestamp_end DATETIME,
    summary TEXT,         -- generated by SLM
    raw_log TEXT,         -- append-only, JSON lines
    importance REAL,
    archived BOOLEAN
);

-- Semantic
CREATE TABLE semantic_facts (
    id TEXT PRIMARY KEY,
    user_id TEXT,
    fact TEXT,
    confidence REAL,
    source_episode_ids TEXT,  -- JSON array
    created_at DATETIME,
    updated_at DATETIME,
    last_accessed DATETIME
);

-- Procedural
CREATE TABLE procedures (
    id TEXT PRIMARY KEY,
    name TEXT,
    content TEXT,  -- Markdown
    version INTEGER,
    updated_at DATETIME
);
```

### 6. Retrieval — how to choose what to inject?

- **BM25**: lexical search on semantic facts
- **sqlite-vec**: vector search (embeddings)
- **Hybrid**: BM25 + cosine, reranked by LLM

For Phase 1 (simple): BM25 sufficient. Phase 2: hybrid + memory SLM.

---

## Decisions to make / POCs

| Decision | Options | Priority |
|----------|---------|----------|
| Consolidation trigger | end of session + nightly batch | P0 |
| SQLite schema v1 | see proposal above | P0 |
| Episodic time-decay | soft decay (score) | P1 |
| Episodic -> semantic promotion | 3 mentions = heuristic rule | P1 |
| Phase 1 retrieval | pure BM25 | P0 |
| Phase 2 retrieval | BM25 + sqlite-vec + reranking | P2 |
| Cross-channel memory | shared by default | P0 |

---

## Risks

- **Context rot**: without forced compaction + consolidation, quality degrades on long conversations. Implement compaction + flush from P0.
- **Over-indexing**: indexing too often = rate limits + saturated VRAM (see Chiti crash). Nightly batch mandatory.
- **Fact hallucination**: the memory SLM can generate incorrect facts. Always keep the `raw_log` as the source of truth.
- **Privacy**: semantic facts are very sensitive. Encryption at rest if multi-user.
