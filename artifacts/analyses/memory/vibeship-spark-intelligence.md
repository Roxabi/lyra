# vibeship-spark-intelligence — Self-Evolving Local AI Companion

> Source: https://github.com/vibeforge1111/vibeship-spark-intelligence
> Tier: 2 (Reference)
> Local clone: ~/projects/external_repo/memory/vibeship-spark-intelligence/
> Visual: [vibeship-spark-architecture.html](vibeship-spark-architecture.html)

## Summary

Spark Intelligence is a 100% local, self-evolving AI companion that continuously converts agent session experience into adaptive operational behavior. It is not a chatbot or fixed rule set — it is a living intelligence runtime that captures events from coding agents (Claude Code, Cursor, Codex, OpenClaw), distills noise into cognitive insights, and delivers contextual advisory guidance before tool actions.

The core learning cycle:

```
You work → Spark captures events → Meta-Ralph quality-gates → distills to cognitive insights
→ transforms for advisory delivery → surfaces advice before next action → outcomes re-enter the loop
```

The key distinction from other memory systems: Spark differentiates between **operational telemetry** (tool sequences, timing — noise) and **cognitive insights** (decisions, domain knowledge, wisdom — signal). Phase 1 cleaned 84% primitive insights (1,196 of 1,427), retaining only 231 cognitive entries.

## Key Components

### Event Capture Layer
- **hooks/observe.py**: Claude Code hooks (PreToolUse, PostToolUse, UserPromptSubmit). Ultra-fast capture with EIDOS integration for prediction loops.
- **sparkd.py**: HTTP ingest endpoint for SparkEventV1 payloads (port 8787).
- **adapters/**: stdin_ingest, clawdbot_tailer, openclaw_tailer, codex_hook_bridge — pluggable event sources.
- **lib/queue.py**: Append-only JSONL queue (`~/.spark/queue/events.jsonl`) with overflow spillover and logical consume head for O(1) consumption.

### Bridge Worker (Processing Loop)
- **bridge_worker.py** + **lib/bridge_cycle.py**: 60s polling loop that reads events, classifies them in a single pass, and routes to:
  - Memory capture (heuristic triggers: "remember this", "I prefer", "always/never")
  - Pattern detection (corrections, sentiment, repetition, semantic similarity, why-chains)
  - Chip processing (domain-specific intelligence modules)
  - Content learning (edit/write event analysis)
  - Prediction/validation loops
- Batches writes to cognitive/meta stores (flush once per cycle, not per event).
- Runtime hygiene: stale heartbeat/PID/tmp cleanup each cycle.

### Quality Gating (Meta-Ralph)
- **lib/meta_ralph.py**: Multi-dimensional scoring on 5 axes: actionability, novelty, reasoning, specificity, outcome_linked (each 0-2 integer, normalized to 0-1).
- Philosophy: "Evolve, don't disable. Roast until it's good."
- The Ralph Loop: PROPOSE -> ROAST -> REFINE -> TEST -> VERIFY -> META-ROAST -> repeat.
- Quality threshold: score >= 4 (sum of 5 dimensions, each 0-2).
- Tracks outcomes to adjust its own gating quality over time (meta-metacognition).

### Distillation & Transformation
- **lib/distillation_transformer.py**: Bridges Meta-Ralph scoring to advisory delivery. Extracts semantic structure (condition, action, reasoning, outcome) from raw text using regex patterns. Computes unified score with dimension weights (actionability: 0.30, reasoning: 0.20, outcome_linked: 0.20, novelty: 0.15, specificity: 0.15).
- **lib/distillation_refiner.py**: Multi-stage refinement: raw score -> elevation transforms -> structure rewrite -> component composition -> optional LLM refinement.
- Suppression layer catches noise that passes Meta-Ralph: session boilerplate, code artifacts, tautologies, no-action/no-reasoning items.

### Cognitive Learner
- **lib/cognitive_learner.py**: 8 learning categories (self-awareness, user-understanding, reasoning, context, wisdom, meta-learning, communication, creativity).
- Each insight has reliability (starts 0.5, +0.1 per validation, -0.2 per contradiction, -0.01/week decay).
- Indexes embeddings on write for semantic retrieval.
- Injection/garbage detection to reject prompt injection attempts.

### EIDOS Loop (Predict-Evaluate-Distill)
- **EIDOS** = Explicit Intelligence with Durable Outcomes & Semantics.
- The Vertical Loop: Action -> Prediction (confidence 0-1) -> Outcome -> Evaluation (PASS/FAIL/PARTIAL) -> Policy Update -> Distillation -> Mandatory Reuse.
- Enforces learning: retrieved memories MUST be cited or action gets blocked.
- Control plane: budget guards (max_steps=25, max_time=720s, max_retries_per_error=2), stuck-state detection, phase control.
- Stored in SQLite (`~/.spark/eidos.db`).

### Advisory Engine
- **lib/advisory_engine.py**: Orchestrates direct-path advisory and predictive packets.
- Hot-path optimized: packet lookup (exact, then relaxed) before live advisor retrieval.
- Semantic retrieval: embeddings-first fast path, selective agentic fanout under minimal gate (weak count, weak score, high-risk terms).
- Fusion scoring: `(similarity * 0.5) + (recency * 0.2) + (effectiveness * 0.3) + priority_boost`.
- Cooldown, dedupe, authority levels (silent/whisper/note/warning/block), fallback rate limiting.
- Advisory packets pre-computed and cached for sub-4s delivery.

### Chip System (Domain Intelligence)
- **lib/chips/**: Pluggable YAML-defined domain modules (game_dev, marketing, fintech, etc.).
- Loader supports single file, multifile bundles, and hybrid specs.
- Router normalizes event aliases and matches trigger patterns.
- Runtime applies pre-storage quality gates; scoring computes cognitive value and promotion tier.
- Evolution: tracks trigger quality, can deprecate/add triggers, suggest provisional chips.
- Chip merger integrates high-quality chip insights into cognitive memory with stable dedupe.

### Promotion & Context Sync
- **lib/promoter.py**: Manual/auto promotion of high-confidence insights (reliability >= 0.7, min 3 validations, confidence floor 0.90) to CLAUDE.md / AGENTS.md / TOOLS.md / SOUL.md.
- **lib/auto_promote.py**: Rate-limited auto-promotion at session end (max once per hour).
- **lib/context_sync.py**: Writes live context to output adapters (openclaw, exports as defaults).

### Observability
- **Obsidian Observatory**: Auto-generated 465+ markdown pages browsable in Obsidian vault. 12-stage pipeline detail pages, explorer for all data stores, Mermaid flow diagrams, Dataview queries.
- **Spark Pulse**: External web dashboard.
- **spark_watchdog.py**: Health, queue size, heartbeat freshness monitoring.

## Memory Evolution Cycle

### 1. Capture
Agent hooks (PreToolUse, PostToolUse, UserPromptSubmit) and adapters emit SparkEventV1 payloads into the event queue. Events carry trace_id for full-chain attribution. The queue is append-only JSONL with overflow spillover and a logical consume head pointer (O(1) consume + periodic compaction).

### 2. Distillation
The bridge cycle (60s loop) reads events, classifies them in a single pass, and routes to specialized processors:
- **Memory capture**: heuristic triggers ("remember this", "I prefer") with scoring (hard triggers 0.65-1.0, soft triggers 0.25-0.75). Auto-save above 0.82, suggest above 0.55.
- **Pattern detection**: aggregator runs correction, sentiment, repetition, semantic, why-chain detectors. Request tracker wraps user requests as EIDOS Steps. Distiller creates typed distillations (heuristic, anti-pattern, sharp edge, playbook, policy).
- **Memory gate**: Weighted scoring (impact 0.30, surprise 0.30, novelty 0.20, recurrence 0.20) with threshold 0.50. High-stakes keywords get automatic pass.
- **Meta-Ralph roast**: 5-dimensional quality scoring before any insight is stored.

### 3. Transformation
The distillation transformer converts raw insights into advisory-ready format:
- Extracts semantic structure (condition, action, reasoning, outcome) via regex.
- Composes clean advisory text: "When {condition}: {action} because {reasoning} ({outcome})".
- Scores unified quality as weighted blend of 5 dimensions.
- LLM areas (optional): actionability_boost, specificity_augment, reasoning_patch, system28_reformulate.
- Aggressive suppression layer: session boilerplate, code artifacts, observation-only items, tautologies without context, unified score below 0.20.

### 4. Storage
Multiple complementary stores:
- **cognitive_insights.json**: Primary insight store with reliability, validation count, timestamps, decay.
- **eidos.db**: SQLite for episodes, steps, predictions, distillations (the vertical loop persisted).
- **semantic/insights_vec.sqlite**: Embedding vectors for cosine similarity retrieval.
- **memory_store.sqlite**: Hybrid SQLite with FTS5 + embeddings + graph edges.
- **banks/*.jsonl**: Per-project and global memory banks.
- **chip_insights/*.jsonl**: Domain-specific learnings with JSONL rotation.
- **advice_packets/**: Pre-computed advisory packets indexed for fast lookup.

### 5. Advisory
The advisory engine is triggered on PreToolUse hooks:
1. Resolve intent and task plane via intent taxonomy.
2. Attempt packet lookup (exact, then relaxed).
3. On miss: fall back to live semantic retrieval (embeddings-first, then selective agentic fanout).
4. Gate: quality, cooldown, dedupe, authority checks.
5. Synthesize and emit advice (deterministic by default, selective AI for high-authority items).
6. Enqueue background prefetch from UserPromptSubmit for future queries.

### 6. Feedback
PostToolUse/PostToolUseFailure capture outcomes:
- **Implicit feedback**: Did the user follow the advice? Tool succeeded/failed?
- **Prediction matching**: Did the EIDOS prediction match the actual outcome?
- **Outcome attribution**: trace_id links outcome to the specific advisory that preceded it.
- **Reliability update**: Positive outcomes increase insight reliability (+0.1), negative decrease (-0.2).
- **Meta-Ralph learning**: Quality gate adjusts its own thresholds based on outcome-linked feedback.
- **Advisory packet invalidation**: Edit/Write outcomes invalidate relevant cached packets.

## Relevance for Lyra

### Direct Mapping to Lyra's 5-Level Memory

| Spark Concept | Lyra Level | Phase | Notes |
|---|---|---|---|
| Event queue + context window | L0 Working | P1 | Raw events being processed in current cycle |
| SPARK_CONTEXT.md + session state | L1 Session | P1 | Multi-turn state per pool/binding |
| Memory banks (per-project JSONL) | L2 Episodic | P2 | Dated, immutable records |
| Cognitive insights + semantic index | L3 Semantic | P1 | Hybrid search (BM25 + embeddings) — already in Lyra spec |
| EIDOS distillations + chip evolution | L4 Procedural | P2/P3 | Learned rules, policies, domain expertise |

### Distillation Equivalent in Lyra

Lyra's consolidation pipeline (`working -> session -> episodic -> semantic`) maps to Spark's flow:

1. **Working -> Session** = Spark's bridge cycle consuming events from queue and writing to session context (SPARK_CONTEXT.md). In Lyra: the `asyncio.Queue` bus processes events and maintains working memory per pool.

2. **Session -> Episodic** = Spark's memory capture heuristics + auto-save on session end. In Lyra: session compaction writes dated Markdown entries to `~/.lyra/episodic/`.

3. **Episodic -> Semantic (promotion)** = Spark's cognitive learner + promoter with reliability gating (>= 0.7, 3+ validations). **This is where Spark's Meta-Ralph is most valuable for Lyra**: a quality gate that scores insights on actionability, reasoning, and outcome linkage before promoting to the semantic store.

4. **Semantic -> Procedural** (Phase 2/3) = Spark's EIDOS distillation + chip system. Turns patterns into rules and policies that actively modify agent behavior.

### Key Insight: Spark's "Distillation" is Lyra's "Consolidation"

The terminology differs but the function is identical:
- **Spark distillation** = filter noise, extract cognitive signal, shape for reuse
- **Lyra consolidation** = promote from one memory level to the next with increasing abstraction

The critical difference: Spark has a quality-gate-per-step architecture (Meta-Ralph at capture, memory gate at pattern detection, distillation transformer at storage, advisory gate at retrieval). Lyra should adopt this "gate at every transition" pattern.

## Actionable Patterns

### P1 (Phase 1) — Implement Now

1. **Quality-Gated Consolidation**: Adopt Spark's multi-dimensional scoring (actionability, reasoning, outcome_linked as minimum 3 axes) at the `session -> episodic` and `episodic -> semantic` boundaries. A simple Python function scoring these 3 dimensions with weighted blend (not LLM-dependent) is sufficient.

2. **Reliability Scoring on Semantic Entries**: Every entry in Lyra's `memory.db` should have:
   - `reliability` (float, starts 0.5, +0.1 on validation, -0.2 on contradiction)
   - `validations` (int, count of positive confirmations)
   - `last_validated` (timestamp)
   - `decay_rate` (configurable per category)
   - Promotion threshold: reliability >= 0.7 AND validations >= 3.

3. **Heuristic Memory Capture**: Port Spark's trigger system: hard triggers ("remember this", "always", "never" -> score 0.65-1.0) and soft triggers ("I prefer", "I need" -> score 0.25-0.55). Auto-save above 0.82, suggest above 0.55. This is purely deterministic, no LLM required.

4. **Semantic Retrieval Fusion Scoring**: Adopt the fusion formula for Lyra's hybrid search:
   ```
   score = (similarity * 0.5) + (recency * 0.2) + (reliability * 0.3) + priority_boost
   ```
   This weights meaning over recency, and past usefulness over raw similarity.

5. **Outcome Attribution via Trace IDs**: Lyra's `asyncio.Queue` events should carry a `trace_id` that flows through the entire processing chain. When advisory is given and an outcome follows, the trace links them for reliability updates.

### P2 (Phase 2) — Consolidation Engine

6. **Prediction-Outcome Loop (Simplified EIDOS)**: Before a skill executes, record a prediction (expected outcome, confidence). After execution, evaluate. Mismatches are surprises — the most valuable learning signals. This is lighter than full EIDOS: no mandatory reuse enforcement, just prediction tracking.

7. **Automatic Session-End Compaction**: At pool deactivation, run a consolidation pass:
   - Extract cognitive signals from session transcript
   - Score with quality gate
   - Write high-signal items to episodic memory
   - Promote items crossing reliability threshold to semantic store

8. **Category-Based Decay Rates**: Different insight types should decay at different rates (Spark uses per-category half-lives). User preferences decay slowly, technical observations decay faster, project-specific context decays when project is inactive.

### P3 (Phase 3) — Self-Evolution

9. **Meta-Learning (Meta-Ralph equivalent)**: The quality gate should track its own accuracy — are the insights it promotes actually useful? This requires outcome-linked validation: when promoted advice is surfaced and followed, does the outcome improve?

10. **Domain Chip Equivalent**: Lyra skills could maintain skill-scoped memory — each skill accumulates domain-specific insights that improve its own behavior. The chip evolution pattern (track trigger quality, deprecate bad triggers, suggest new ones) maps to skill self-improvement.

11. **Contradiction Detection**: When a new insight contradicts an existing one, don't overwrite — flag the contradiction and require resolution. Spark's `lib/contradiction_detector.py` and `lib/hypothesis_tracker.py` provide the template.

## Risks & Limitations

### Over-Engineering Risk
Spark has ~80+ Python modules, 20+ data stores, and massive configuration surface. This is excessive for Lyra P1. The core value is in 3 things: quality gating, reliability scoring, and outcome attribution. Everything else is optimization.

### Coupling to Claude Code Hooks
Spark's architecture is deeply coupled to Claude Code's hook system (PreToolUse/PostToolUse). Lyra's bus-based architecture is more flexible — events flow through `asyncio.Queue` with channel-agnostic adapters. Don't adopt Spark's hook-specific code; adapt the patterns to Lyra's bus model.

### LLM-in-the-Loop Quality Gates
Spark's Meta-Ralph and distillation refiner have optional LLM calls for quality improvement. This adds latency and cost. For Lyra P1, stick to deterministic scoring (regex patterns, keyword matches, heuristic weights). LLM refinement is a P3 optimization.

### File-Based State (Not SQLite)
Spark uses JSON files with file-locking for many stores (cognitive_insights.json, predictions.jsonl). This works for single-user but is fragile. Lyra already uses SQLite + aiosqlite — keep everything in the database with proper transactions.

### Complexity of Advisory Engine
Spark's advisory engine has packets, fallback chains, rate guards, budget caps, programmatic vs AI synthesis, and packet invalidation. For Lyra P1, a simple "retrieve top-3 relevant insights before skill execution" is sufficient.

### Premium/OSS Split
Chip self-evolution is marked as premium-only. Some patterns referenced in docs may not be fully implemented in the OSS codebase.

### Single-User Design
Spark is designed for one user's local machine. Lyra needs multi-user memory isolation (per-binding scoping). Spark's approach of global `~/.spark/` stores won't work — Lyra needs per-user, per-pool, per-channel memory boundaries.

## Priority

**Phase 1**: Quality-gated consolidation, reliability scoring, heuristic memory capture, fusion retrieval scoring, trace-based outcome attribution. These are the core mechanisms that make memory useful rather than just stored.

**Phase 2**: Prediction-outcome loop (simplified EIDOS), session-end compaction, category-based decay. These make consolidation automatic and self-correcting.

**Phase 3**: Meta-learning (quality gate self-assessment), skill-scoped memory (chip equivalent), contradiction detection, domain-specific chip evolution. These make the system truly self-evolving.

The most impactful single pattern from Spark: **reliability scoring with outcome-linked feedback**. An insight starts at 0.5 reliability, gains 0.1 per positive outcome, loses 0.2 per negative outcome, and decays 0.01/week without use. This simple mechanism ensures the memory naturally converges on what's actually useful.
