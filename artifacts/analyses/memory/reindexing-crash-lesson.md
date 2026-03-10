# Reindexing Crash Lesson (QMD + Qdrant)

> Source: https://x.com/code_rams/status/2023514892297531863
> Tier: 1 (Directly actionable)
> KB ID: mem-reindex-006

## Summary

A developer built a bot using QMD (Quick Markdown Database) combined with Qdrant for vector search. To keep the search index fresh, they configured force re-indexing every 30 minutes. This triggered a cascade of problems: Qdrant rate limits were hit, the indexing process consumed excessive resources, and the system crashed repeatedly. Each crash left the index in an inconsistent state, requiring a full rebuild, which triggered more rate limits — a vicious cycle.

The solution was simple but counterintuitive for developers used to real-time systems: slow down dramatically. Re-indexing frequency was reduced from 30 minutes to 6 hours. Force rebuild was removed entirely — incremental updates replaced full re-indexing. The developer's key realization was that indexation should be treated like backups, not like heartbeats. Backups are infrequent, thorough, and resilient. Heartbeats are frequent, lightweight, and disposable. Indexing has the resource profile of backups but was being treated as heartbeats.

The root cause was a fundamental misunderstanding of the cost/benefit ratio of indexing freshness. For a knowledge base that changes a few times per day, re-indexing every 30 minutes provides zero benefit (no new data to index) at maximum cost (full resource consumption). The 6-hour cadence provides identical freshness with 1/12th the resource cost.

## Key Insights

- Indexation is like backups, not heartbeats: infrequent, thorough, resilient
- Force re-indexing is almost never necessary — incremental updates suffice
- Rate limits and crashes from over-indexing create a vicious cycle (crash -> inconsistent state -> needs full rebuild -> more rate limits)
- Knowledge bases change slowly — re-indexing frequency should match data change frequency, not developer anxiety
- 30min -> 6h = identical freshness, 12x less resource usage, zero crashes
- The "real-time" instinct from web development does not apply to knowledge indexing

## Relevance for Lyra Memory

**Levels impacted**: Level 2→3 consolidation scheduler, Level 3 indexing.

This is the hard constraint for our consolidation scheduler. The lesson is unambiguous: nightly batch consolidation, NEVER real-time. This was already a decision in `02-memory.md`, but this incident provides the concrete justification.

For Lyra specifically:
- **L1 -> L2 (session -> episodic)**: Can be real-time (triggered by session end) because it is a simple append, not an indexing operation. Low cost, event-driven.
- **L2 -> L3 (episodic -> semantic)**: MUST be nightly batch. This involves fact extraction, BM25 index update, and embedding generation. These are expensive operations.
- **L3 re-indexing**: Never force rebuild. Incremental updates only. If an embedding model changes, re-index gradually over multiple nights.

On our RTX 3080 (10GB VRAM), embedding generation competes with TTS for VRAM. Nightly batch means we can run consolidation when TTS is idle (e.g., 3 AM).

## Actionable Items

- **[Scheduler, Phase 1]** Implement consolidation as a nightly cron job (e.g., 3:00 AM). Single execution per night. No retry loops — if it fails, it waits until the next night.
- **[Level 3, Phase 1]** Consolidation must be incremental: only process L2 episodes that are new since the last consolidation run. Track `last_consolidated_at` timestamp.
- **[Level 3, Phase 1]** Never implement force re-index. If the schema changes, add a migration that marks all entries as "needs re-indexing" and let the nightly batch process them gradually (N entries per night, configurable).
- **[VRAM, Phase 1]** Schedule consolidation during TTS idle time. The nightly batch should check if VRAM is available before running embedding generation. If TTS is active, defer to next idle window.
- **[Monitoring, Phase 1]** Log consolidation duration, entries processed, and any failures. Alert if consolidation takes longer than 1 hour (indicates growing backlog or performance issue).
- **[Architecture, Phase 1]** Document this as a hard constraint: "Consolidation is a nightly batch. Real-time consolidation is forbidden. See reindexing-crash-lesson.md for rationale."

## Priority

**Phase 1 (mandatory)**: The nightly batch scheduler is a P0 deliverable. This is not an optimization — it is a stability requirement. Real-time consolidation will crash the system on our hardware (single RTX 3080, shared with TTS).
