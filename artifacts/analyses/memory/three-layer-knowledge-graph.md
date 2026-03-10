# Three-Layer Knowledge Graph (Clawdbot Upgrade)

> Source: https://x.com/i/article/2015965409682587648
> Tier: 1 (Directly actionable)
> KB ID: mem-threelayer-008

## Summary

This article describes an upgrade from static file-based agent memory to a three-layer knowledge system. The first layer is a Knowledge Graph with entity-based storage and atomic facts — each fact is a standalone unit linked to entities (people, projects, concepts) rather than stored in monolithic documents. The second layer is Daily Notes, which serve as raw event logs — timestamped, unprocessed records of what happened each day. The third layer is Tacit Knowledge, capturing preferences, lessons learned, and behavioral patterns that emerge over time.

The system features a compounding engine: a Haiku-class sub-agent runs approximately every 30 minutes to extract atomic facts from recent conversations and add them to the Knowledge Graph. Weekly, a more thorough synthesis runs (Sunday batch) that consolidates the week's Daily Notes into higher-level insights, prunes redundant facts, and strengthens cross-entity relationships. This creates a self-maintaining, compounding knowledge base where the system gets smarter over time without manual curation.

The three layers serve different retrieval patterns: the Knowledge Graph is queried by entity or relationship ("what do I know about Project X?"), Daily Notes are queried by time ("what happened yesterday?"), and Tacit Knowledge is injected automatically based on behavioral context ("user prefers concise responses").

## Key Insights

- Atomic facts linked to entities > monolithic documents — enables graph traversal and relationship queries
- Daily Notes as raw logs preserve ground truth — synthesized facts can always be traced back to source
- Tacit Knowledge is the hardest layer — it emerges from patterns, not explicit statements
- The compounding engine (sub-agent every 30min) keeps the knowledge graph fresh, but has resource implications
- Weekly synthesis (Sunday batch) provides a rhythm for deeper consolidation
- The 3-layer structure naturally separates retrieval patterns: entity-based, time-based, context-based

## Relevance for Lyra Memory

**Levels impacted**: Level 2 (episodic), Level 3 (semantic), Level 4 (procedural), and the consolidation pipeline.

The three layers map cleanly to Lyra's levels:

| Three-Layer System | Lyra Level | Notes |
|---|---|---|
| Daily Notes (raw logs) | L2 Episodic | Same purpose: dated, raw, preserves context |
| Knowledge Graph (atomic facts) | L3 Semantic | Same purpose: entity-based, queryable facts |
| Tacit Knowledge (preferences) | L4 Procedural | Same purpose: emergent behavioral patterns |

The compounding engine design is instructive but we must adapt it. The article uses a 30-minute Haiku sub-agent, but the reindexing crash lesson (mem-reindex-006) proves this is too aggressive. Our adaptation:

| Original | Lyra Adaptation |
|----------|-----------------|
| Haiku every 30min | Nightly batch (L2 -> L3 fact extraction) |
| Sunday weekly synthesis | Weekly batch (L3 deduplication + L3 -> L4 promotion) |
| Real-time Knowledge Graph updates | Incremental nightly updates only |

The entity-based storage model is valuable. Instead of storing semantic facts as flat text, we should consider entity linking: each fact references one or more entities (user, project, tool, concept). This enables graph-style queries in Phase 2.

## Actionable Items

- **[Level 3 Schema, Phase 1]** Add entity linking to semantic facts. Extend the schema with an `entities` field (JSON array of entity references). Example: `{"fact": "Mickael prefers Python", "entities": ["user:mickael", "language:python"]}`.
- **[Level 2, Phase 1]** Treat L2 episodic entries as Daily Notes: raw, timestamped, append-only. Never modify or summarize in place — summarization produces a new L3 fact, the original L2 entry stays intact.
- **[Consolidation, Phase 1]** Implement nightly fact extraction: the consolidation job reads new L2 episodes, extracts atomic facts, and inserts them into L3 with entity links and source references.
- **[Consolidation, Phase 2]** Implement weekly synthesis: every Sunday (or configurable day), run a deeper consolidation that deduplicates L3 facts, strengthens entity relationships, and identifies patterns for L4 promotion.
- **[Level 4, Phase 2]** Tacit knowledge detection: the weekly synthesis should look for patterns across multiple semantic facts (e.g., "user always asks for voice output in French" -> procedural rule: default voice language = French).
- **[Level 2, Phase 1]** Preserve raw logs as ground truth. L2 entries must be immutable after creation. Any corrections or updates produce new entries, not modifications.

## Priority

**Phase 1 (partial)**: Entity linking in L3 schema and nightly fact extraction are Phase 1 deliverables. The weekly synthesis and tacit knowledge detection are Phase 2.
**Phase 2 (full)**: Weekly synthesis, L3 -> L4 promotion, and graph-style queries over entity relationships.
