# gigabrain -- Long-term Memory Layer (SQLite + Markdown)

> Source: https://github.com/legendaryvibecoder/gigabrain
> Tier: 1 (Directly actionable)
> Local clone: ~/projects/external_repo/memory/gigabrain/
> Architecture diagram: [gigabrain-architecture.html](./gigabrain-architecture.html)

## Summary

Gigabrain is a long-term memory layer for AI agents built on the OpenClaw platform. It converts conversations and native markdown notes into durable, queryable memory, then injects relevant context before each prompt so agents stay consistent across sessions.

Key design philosophy: **local-first, deterministic by default, LLM-optional**. The entire capture/recall pipeline runs without any embedding model or LLM. Similarity is computed via token-level Jaccard (word overlap + char trigrams + numeric tokens + semantic anchors), not vector embeddings. An LLM (Ollama/OpenAI) is only used optionally for audit second opinions on borderline quality scores.

Architecture pattern: **Event Sourcing + CQRS**. Every memory operation (capture, dedupe, archive, restore) appends an immutable event to `memory_events`. The `memory_current` table is a materialized projection of current state, rebuilt from events if needed.

Stack: Node.js >= 22 using the experimental `node:sqlite` API (synchronous `DatabaseSync`), TypeScript entry point (`index.ts`), JavaScript core services (`lib/core/*.js`). Optional FastAPI web console in Python.

## Key Components

### Core Services (`lib/core/`)

| File | Role |
|------|------|
| `config.js` | 1000-line config normalization with deep merge, legacy key rejection, path resolution, budget normalization. Extremely thorough validation. |
| `capture-service.js` | Parses `<memory_note>` XML tags from agent output. Handles junk filtering, exact/semantic dedupe, quality gates, plausibility checks. Routes to registry and/or native markdown based on durable/ephemeral classification. |
| `recall-service.js` | Query sanitization, entity coreference resolution, dual-source search (registry + native chunks), multi-signal ranking, budget allocation by class, XML injection into conversation. |
| `event-store.js` | Append-only event log. Every mutation creates an event with `event_id`, `component`, `action`, `reason_codes`, `run_id`, `review_version`, `similarity`, `payload`. |
| `projection-store.js` | Materialized current-state view (`memory_current`). Supports upsert, status update, search with lexical scoring. Keeps legacy `memories` table in sync. |
| `native-sync.js` | Indexes workspace markdown files (MEMORY.md, daily notes, curated files) into `memory_native_chunks`. Detects changes via mtime/size/hash. Skips internal context blocks, transcript lines, recall artifacts. |
| `native-promotion.js` | Promotes durable native chunks to structured registry memories with `source_layer: promoted_native` provenance. Skips CONTEXT/EPISODE types and ephemeral content. |
| `native-memory.js` | Writes captured memories back to native markdown files (MEMORY.md for durable, daily notes for ephemeral). Appends bullets under typed section headings. Deduplicates by normalized content. |
| `person-service.js` | Entity mention graph. Extracts proper names, classifies roles (relationship/public_profile/ops_noise), builds `memory_entity_mentions` table. Enables entity-aware recall with priority boosts. |
| `policy.js` | Quality policy engine. Junk patterns (32 base), durable patterns, plausibility heuristics (broken phrases, entityless numeric facts), 9-feature value scoring, composite Jaccard similarity. |
| `audit-service.js` | Quality scoring with optional LLM second opinion. Review ledger in `memory_quality_reviews`. Shadow/Apply/Restore modes. Idempotent (skips unchanged scores). |
| `maintenance-service.js` | Nightly pipeline: snapshot -> native_sync -> native_promotion -> quality_sweep -> exact_dedupe -> semantic_dedupe -> audit_delta -> archive_compression -> vacuum -> metrics_report -> vault_build. |
| `vault-mirror.js` | Obsidian memory surface builder. Generates 00 Home, 10 Native, 20 Nodes, 30 Views, 40 Reports. Read-only, supports remote pull via rsync. |
| `llm-router.js` | LLM provider abstraction. Supports Ollama, OpenAI-compatible, OpenClaw. Task profiles with per-job temperature/top_p/top_k/max_tokens. Used only for audit review, not core pipeline. |
| `review-queue.js` | JSONL-based review queue for borderline captures. Retention policy with configurable max rows, max age, relevant reason filtering. |
| `http-routes.js` | Gateway HTTP endpoints: `/gb/health`, `/gb/recall`, `/gb/suggestions`, `/gb/bench/recall`, `/gb/memory/:id/timeline`. Token auth with timing-safe comparison. |
| `metrics.js` | Telemetry counters for nightly pipeline. Snapshot metrics, usage log rendering. |
| `sqlite.js` | Thin wrapper around `node:sqlite` with busy timeout configuration. |

### Plugin Entry Point (`index.ts`)

Registers two hooks on the OpenClaw gateway:
- `before_agent_start`: Extracts user query, sanitizes it, enriches with entity context, runs recall, injects results as a system message before the last user message.
- `agent_end`: Extracts agent output, parses memory notes, runs capture pipeline.

On startup: opens DB, ensures all tables, runs native sync + promotion, rebuilds entity mentions.

### Web Console (`memory_api/app.py`)

Optional FastAPI dashboard. Features: dual-surface landing, memory browser with search/filter, concept deduplication view, audit queue, document store (PDF/URL ingest), profile viewer, knowledge graph visualization.

## Memory Architecture

### Storage Schema

**4 core tables + 2 auxiliary:**

```
memory_events (append-only event log)
  event_id TEXT PK, timestamp, component, action, reason_codes JSON,
  memory_id, cleanup_version, run_id, review_version,
  similarity REAL, matched_memory_id, payload JSON

memory_current (materialized projection)
  memory_id TEXT PK, type, content, normalized, normalized_hash,
  source, source_agent, source_session, source_layer, source_path, source_line,
  confidence REAL, scope, status (active/archived/rejected/superseded),
  value_score REAL, value_label, created_at, updated_at, archived_at,
  tags JSON, superseded_by, content_time, valid_until

memory_native_chunks (indexed markdown)
  chunk_id TEXT PK (sha1 of path|lines|normalized),
  source_path, source_kind (memory_md/daily_note/curated),
  source_date, section, line_start, line_end,
  content, normalized, hash, linked_memory_id,
  first_seen_at, last_seen_at, status

memory_entity_mentions (entity graph)
  id TEXT PK, memory_id, entity_key, entity_display,
  role (relationship/public_profile/ops_noise/general),
  confidence REAL, source (memory_current/memory_native)

memory_quality_reviews (audit ledger)
  id TEXT PK, memory_id, reviewed_at, review_version,
  action, score REAL, reason_codes JSON,
  before_status, after_status, features JSON

memory_native_sync_state (change detection)
  source_path TEXT PK, mtime_ms, size_bytes, hash, last_synced_at
```

### Memory Types

7 types with distinct base utility scores and dedupe thresholds:

| Type | Utility | Auto Dedupe | Review Dedupe | Notes |
|------|---------|-------------|---------------|-------|
| AGENT_IDENTITY | 1.00 | 0.82 | 0.66 | Always kept, highest priority |
| PREFERENCE | 0.92 | 0.78 | 0.62 | Durable, personal |
| USER_FACT | 0.90 | 0.78 | 0.62 | Most common type |
| ENTITY | 0.85 | 0.82 | 0.66 | Person/place facts |
| DECISION | 0.75 | 0.82 | 0.66 | Project decisions |
| EPISODE | 0.68 | 0.82 | 0.66 | Temporal events |
| CONTEXT | 0.50 | 0.84 | 0.70 | Lowest priority, situational |

### Capture Flow

1. Agent emits `<memory_note type="USER_FACT" confidence="0.9">content</memory_note>` in its response
2. `captureFromEvent()` parses all memory_note tags from agent output
3. If no tags found but remember intent detected -> queue for review
4. For each note:
   - Normalize type, parse confidence (high/medium/low or 0.0-1.0)
   - Determine capture mode: durable vs ephemeral, native vs registry
   - Run junk filter (32 patterns + metadata noise detection)
   - Run plausibility check (broken phrases, entityless numerics, low-confidence generics)
   - Check exact duplicate (normalized hash match within scope)
   - Check semantic duplicate (Jaccard similarity with type-aware thresholds)
   - If passes: upsert to `memory_current`, append event, optionally write native markdown

### Recall Flow

1. Extract last user message, sanitize (strip prior gigabrain-context, metadata, bootstrap injections)
2. Entity coreference: if pronoun follow-up detected, enrich query with entity from prior messages
3. Detect temporal window (month names, year references)
4. Resolve entity keys from `memory_entity_mentions` table
5. Build query signals: focus tokens (stopword-filtered), entity intent detection
6. **Dual-source search:**
   - Registry: LIKE queries on `memory_current` (content OR normalized contains token)
   - Native: chunk search on `memory_native_chunks` with entity + temporal filters
7. Rank all results with multi-signal scoring (semantic overlap + value score + recency + scope + durable + entity + person - noise - archive - stale time)
8. Prioritize entity rows if entity intent detected
9. Dedupe by normalized content
10. Allocate by budget: core 45% / situational 30% / decisions 25% (token-based)
11. Render `<gigabrain-context>` XML block and inject as system message before last user message

### Consolidation (Nightly Maintenance)

Fixed 11-step sequence, all within one `runMaintenance()` call:

1. **Snapshot**: Emergency + compact SQLite backup
2. **Native Sync**: Re-index all markdown files, detect changes by mtime/size/hash
3. **Native Promotion**: Promote new/changed durable chunks to registry
4. **Quality Sweep**: `classifyValue()` on every active memory, archive/reject low-quality
5. **Exact Dedupe**: Group by scope + normalized content, keep highest priority
6. **Semantic Dedupe**: Pairwise Jaccard within scope+type, archive losers above threshold
7. **Audit Delta**: Write review ledger rows, emit events
8. **Archive Compression**: Write summary artifacts (markdown, JSONL, CSV)
9. **Vacuum**: SQLite VACUUM
10. **Metrics Report**: Capture snapshot metrics, append usage log
11. **Vault Build**: Generate Obsidian memory surface

### Deduplication Strategy

**No embedding model.** All similarity is token-level:

```
jaccardSimilarity(a, b) = weighted average of:
  - Word overlap (Jaccard of tokenized words): weight 0.35
  - Char trigram overlap: weight 0.25
  - Numeric token overlap: weight 0.20
  - Semantic anchor overlap (predefined anchor words): weight 0.20
```

Two-tier thresholds:
- Above `autoThreshold` (default 0.92, lower for USER_FACT/PREFERENCE): auto-merge silently
- Between `reviewThreshold` and `autoThreshold`: queued for review
- Below `reviewThreshold`: accept as distinct

## Relevance for Lyra

### Level 0 (Working Memory) -- Not addressed by gigabrain
Gigabrain does not manage the context window. It injects content into it via recall, but working memory management (compaction, summarization) is outside its scope.

### Level 1 (Session Memory) -- Minimal coverage
Gigabrain tracks `source_session` on memories but does not maintain per-session state. Session-level context is handled by the agent framework (OpenClaw), not gigabrain. The only session-aware behavior is scope resolution from session keys.

### Level 2 (Episodic Memory) -- Partial coverage via native sync
Daily notes (`YYYY-MM-DD.md`) serve as dated episodic records. Native sync indexes them and makes them searchable. However, gigabrain does not generate these notes automatically from conversation -- the agent must explicitly emit `<memory_note>` tags with EPISODE type.

**Lyra mapping**: This is the weakest match. Lyra's episodic memory should be auto-generated from conversation summaries, not require explicit agent tagging.

### Level 3 (Semantic Memory) -- Strong coverage
This is gigabrain's core strength. The registry (`memory_current`) is a structured semantic store with:
- Typed memories (7 types)
- Quality scoring and value classification
- Scope-based access control
- Lexical search with multi-signal ranking
- Hybrid recall (registry + native chunks)
- Entity-aware retrieval

**Lyra mapping**: Directly usable patterns for Level 3. The SQLite schema, quality policy, recall ranking, and dedupe strategy are all reusable.

### Level 4 (Procedural Memory) -- Not addressed
Gigabrain stores facts and decisions but does not track learned skills, tool usage patterns, or behavioral preferences in a structured way. PREFERENCE and DECISION types partially cover this, but there is no explicit skill graph or procedure memory.

### Component Mapping

| Gigabrain Component | Lyra Level | Reusability |
|---------------------|-----------|-------------|
| `memory_current` schema | L3 Semantic | High -- adapt columns, add embeddings |
| `memory_events` event sourcing | L3 Semantic | High -- exact pattern reusable |
| `memory_native_chunks` | L2 Episodic | Medium -- need auto-generation, not just indexing |
| `memory_entity_mentions` | L3 Semantic | High -- entity graph directly reusable |
| Recall multi-signal ranking | L3 Semantic | High -- add embedding similarity as a signal |
| Jaccard deduplication | L3 Semantic | Medium -- supplement with embedding cosine similarity |
| Quality policy (9-feature scoring) | L3 Semantic | High -- directly reusable |
| Nightly maintenance pipeline | L3 Semantic | High -- adapt for Lyra's async architecture |
| `<memory_note>` capture protocol | L0 Working -> L3 | Medium -- Lyra should use CognitiveFrame, not XML tags |
| Review queue (JSONL) | L3 Semantic | Low -- Lyra should use SQLite for everything |
| Vault mirror (Obsidian) | N/A | Low -- different surface needs |
| Web console (FastAPI) | N/A | Low -- Lyra has its own UI plans |

## Actionable Patterns

### 1. Event Sourcing + CQRS (directly adopt)

**Pattern**: Every memory mutation appends an immutable event. Current state is a materialized projection.

**Files**: `lib/core/event-store.js` (lines 3-23 for schema, lines 57-84 for `appendEvent`), `lib/core/projection-store.js` (lines 68-100 for schema, lines 116-267 for upsert)

**Why adopt**: This gives Lyra full auditability, the ability to rebuild state, and safe rollback. The `memory_events` table is simple and the projection materialization is straightforward.

**Adaptation for Lyra**: Use `aiosqlite` instead of `node:sqlite`. The schema translates directly. Add an `embedding` BLOB column to `memory_current` for sqlite-vec.

### 2. Multi-Signal Recall Ranking (directly adopt)

**Pattern**: Score = semantic_match + value_score + recency_decay + scope_weight + durable_boost + entity_boost + person_boost - noise_penalty - archive_penalty - stale_time_penalty

**File**: `lib/core/recall-service.js` (lines 325-366 for `rankActiveRow`, lines 434-455 for `allocateByBudget`)

**Why adopt**: This ranking formula is production-tested and handles edge cases well (entity queries, temporal safety, stale relative dates). Lyra can add embedding cosine similarity as an additional signal.

**Adaptation for Lyra**: Add `embedding_similarity` signal (weight ~0.4) from sqlite-vec. Reduce `semantic_match` (Jaccard) weight accordingly. Keep all other signals.

### 3. Quality Policy with Value Classification (directly adopt)

**Pattern**: 9-feature vector -> weighted score -> keep/archive/reject classification, with durable/relationship overrides.

**File**: `lib/core/policy.js` (lines 318-375 for `computeFeatures`, lines 363-375 for `computeValueScore`, lines 377-527 for `classifyValue`)

**Why adopt**: Prevents memory pollution. The weighted scoring is well-calibrated for personal facts, relationships, and agent identity. The durable pattern bypass is essential for Lyra's personal nature.

### 4. Native Markdown Sync + Promotion (partially adopt)

**Pattern**: Index markdown files into chunks, promote durable chunks to structured registry with provenance.

**Files**: `lib/core/native-sync.js` (lines 107-220 for chunk parsing, lines 304-443 for sync), `lib/core/native-promotion.js` (lines 139-270 for promotion)

**Why adopt**: Lyra's Level 2 episodic memory will be dated Markdown files. This pattern already handles indexing them, detecting changes, and promoting important content to the semantic layer.

**Adaptation for Lyra**: Auto-generate session summaries as dated markdown (Lyra's episodic layer), then use native sync to index them. Remove the "require explicit `<memory_note>` tag" requirement -- Lyra should extract facts automatically.

### 5. Entity Mention Graph (directly adopt)

**Pattern**: Extract proper names, classify roles, build mention table, use for entity-aware recall.

**File**: `lib/core/person-service.js` (lines 157-237 for `ensurePersonStore` + `rebuildEntityMentions`, lines 239-288 for `resolveEntityKeysForQuery`)

**Why adopt**: Lyra is a personal agent that needs to remember people. The role classification (relationship/public_profile/ops_noise) and priority boosting are directly useful.

### 6. Nightly Batch Consolidation Sequence (adapt)

**Pattern**: Fixed 11-step sequence with snapshots, sync, quality sweep, dedupe, vacuum.

**File**: `lib/core/maintenance-service.js` (lines 30-42 for sequence, lines 295-832 for `runMaintenance`)

**Why adopt**: Lyra's Phase 1 specifies nightly batch consolidation. This pattern is battle-tested and includes proper error handling, artifact generation, and backup management.

**Adaptation for Lyra**: Make it async (`aiosqlite`). Run as a scheduled task in the hub's event loop. Add embedding re-computation step. Skip vault build (Lyra doesn't need Obsidian surface).

### 7. Composite Jaccard Similarity (supplement, don't replace)

**Pattern**: 4-component weighted similarity without embeddings.

**File**: `lib/core/policy.js` (lines 529-554 for `jaccardSimilarity`)

**Why adopt as supplement**: Useful as a fast first-pass filter before expensive embedding comparison. For Lyra, use Jaccard for exact/near-exact dedup (cheap), then embedding cosine for semantic dedup (expensive but more accurate).

## Risks & Limitations

### 1. No Embedding Model -- Limited Semantic Understanding
Gigabrain's recall relies entirely on token overlap (LIKE queries + Jaccard). This misses semantically related but lexically different content. "I love coffee" won't match a query about "favorite beverages". Lyra's BM25 + sqlite-vec hybrid search is strictly superior here.

### 2. Node.js + Synchronous SQLite -- Incompatible Runtime
Gigabrain uses `node:sqlite` (synchronous `DatabaseSync`). Lyra is Python + asyncio. The SQLite schema and algorithms are reusable, but not the code. Everything must be reimplemented in Python with `aiosqlite`.

### 3. Explicit Capture Only -- No Automatic Extraction
Gigabrain only captures memories when the agent explicitly emits `<memory_note>` tags. If the agent forgets the tag, the memory is lost (or queued for review). Lyra should extract important facts automatically from conversation, using SLMs for NER and fact extraction.

### 4. Flat Memory Model -- No Hierarchical Consolidation
Memories go from capture to registry directly. There is no working -> session -> episodic -> semantic consolidation hierarchy. Lyra's 5-level architecture requires explicit level transitions with summarization at each step.

### 5. Single-Machine, Single-Process
Gigabrain assumes a single Node.js process accessing the SQLite database. Lyra's hub-and-spoke architecture with multiple channels will need proper async locking (already planned with `asyncio.Lock` per pool).

### 6. English + German Focus
The recall service has hardcoded English + German stopwords, pronoun detection, and entity patterns. Lyra should be language-agnostic or at least support French as the primary language.

### 7. No Conversation Context for Recall
Gigabrain's recall query comes from the last user message only. It does not consider the full conversation context. Lyra should use the full session state (recent messages + active entities) for recall queries.

### 8. Review Queue is JSONL File, Not SQLite
The review queue uses a flat JSONL file with manual retention logic. This is fragile. Lyra should use a SQLite table for all queue/review data.

## Priority

### Phase 1 (Implement Now)

These patterns should be implemented in Lyra Phase 1 (Levels 0 + 3):

1. **Event sourcing schema** (`memory_events` + `memory_current` adapted for Python/aiosqlite, add `embedding` column)
2. **Quality policy** (9-feature scoring, durable/relationship overrides, junk filter)
3. **Multi-signal recall ranking** (add embedding similarity as primary signal)
4. **Entity mention graph** (adapt person service for French + English)
5. **Nightly batch consolidation** (async version of the 11-step pipeline, add embedding computation step)
6. **Composite deduplication** (Jaccard as fast filter + embedding cosine for semantic)

### Phase 2 (When Adding Levels 1 + 2)

1. **Native sync + promotion** (adapt for auto-generated session summaries)
2. **Episodic markdown generation** (extend native-memory.js pattern for auto-summarized sessions)
3. **Session-aware scope resolution** (extend scope model for Lyra's pool/binding system)

### Phase 3 (Optimization)

1. **LLM audit review** (adapt llm-router for local SLMs on Machine 2)
2. **Harmonization** (adapt the harmonize script for cross-level memory consolidation)
3. **Temporal query handling** (adapt temporal window detection for French dates/months)
