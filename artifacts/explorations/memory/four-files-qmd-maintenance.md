# Four Files + QMD Maintenance Pattern

> Source: https://x.com/austin_hurwitz/status/2023726021858783330
> Tier: 2 (Reference)
> KB ID: mem-four-files-002

## Summary

Austin Hurwitz proposes a 3-step approach for persistent AI memory: (1) install QMD, a local search tool for markdown files, (2) separate memory into 4 distinct files -- long-term memory, daily notes, active tasks, and lessons learned -- and (3) set up maintenance crons to keep the system healthy over time.

The method is deliberately simple. Each of the 4 files has a clear purpose and lifecycle: long-term memory captures durable facts, daily notes capture ephemeral observations, active tasks track current work, and lessons store hard-won insights. This separation prevents the common problem of a single monolithic MEMORY.md growing unbounded and becoming noisy.

A companion skill file describes the session workflow: read all 4 files at startup (boot context), write immediately when new information is observed (no batching during the session), and search via `qmd query` when the agent needs to recall something not in the active context. The maintenance crons handle compaction and archival of stale daily notes.

## Key Insights

- 4-file separation enforces discipline: each file has one purpose, one lifecycle, one metabolic rate
- Read-at-startup pattern ensures the agent is never amnesic at session begin
- Write-immediately pattern prevents loss of observations if a session crashes
- QMD provides local BM25 search over markdown without any cloud dependency
- Maintenance crons handle the "garbage collection" problem that most systems ignore
- The skill file acts as a procedural memory document -- the agent's own instructions on how to use its memory

## Relevance for Lyra Memory

**Levels impacted**: Level 0 (working), Level 1 (session), Level 2 (episodic), Level 3 (semantic), Level 4 (procedural).

The 4-file separation maps almost directly to Lyra's memory levels:

| Austin's file | Lyra level | Notes |
|--------------|------------|-------|
| Long-term memory | Level 3 (semantic) | Durable facts, preferences |
| Daily notes | Level 2 (episodic) | Dated observations, immutable |
| Active tasks | Level 1 (session) | Current work state per pool |
| Lessons | Level 4 (procedural) | Learned patterns and skills |

The maintenance cron pattern validates our nightly batch consolidation decision. Austin's approach shows that even simple file-based systems need scheduled cleanup -- our SQLite-based system needs it even more (index maintenance, embedding re-computation, fact deduplication).

The session workflow pattern (read at start, write immediately, search on demand) is directly applicable to pool initialization in Lyra. When `get_or_create_pool()` fires, we should load the relevant semantic facts for that user into working memory.

## Applicable Patterns

- **Boot context injection** (Level 0): At pool creation, read relevant semantic facts + active session state and inject into the agent's system prompt. Maps to our `agent.process(msg, pool)` call.
- **Write-immediate for observations** (Level 2): During a session, episodic observations should be persisted immediately via `aiosqlite`, not buffered in memory. If the process crashes, nothing is lost.
- **Scheduled maintenance** (Levels 2-3): Nightly batch for: archiving old episodes (time-decay), promoting facts from episodic to semantic (3-mention rule), deduplicating semantic facts, recomputing stale embeddings.
- **Skill file as procedural seed** (Level 4): The "skill file" that tells the agent how to use its own memory is essentially a Level 4 procedural entry. Lyra should have a built-in procedure for memory self-management.

## Priority

**Phase 1**: The read-at-startup and write-immediately patterns should be implemented from the start in the pool/session lifecycle. The nightly batch scheduler is a Phase 1 deliverable (already decided). The 4-file conceptual separation validates our schema design (episodes, semantic_facts, procedures tables).

**Phase 2**: The QMD search integration is not applicable (we use SQLite hybrid search), but the concept of a unified search interface across all memory levels is relevant when the memory SLM is introduced.
