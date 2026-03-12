# ClawVault: Markdown-Native Persistent Memory

> Source: https://x.com/theturingpost/status/2031152502822109473
> Tier: 2 (Reference)
> KB ID: mem-clawvault-native-009

## Summary

The Turing Post highlights ClawVault as a markdown-native persistent memory system for AI agents. The tool provides a knowledge graph, local-first operation (no cloud dependency), session checkpoints, hybrid search, and structured fact extraction -- all stored as human-readable Markdown files that are git-compatible.

ClawVault's core philosophy is that memory should be transparent: every fact, decision, and observation is stored as a Markdown file that a human can read, edit, and version-control with git. This contrasts with opaque vector databases or cloud services where the agent's memory is a black box. The knowledge graph emerges from wiki-links between files, creating an entity-relationship structure without requiring a dedicated graph database.

The checkpointing system allows agents to save their working state mid-session and restore it later. Combined with the wake/sleep lifecycle commands, this creates a robust session management pattern where no context is lost even if the agent process crashes. Search combines BM25 keyword matching with semantic embeddings via Reciprocal Rank Fusion (RRF).

## Key Insights

- Markdown-native storage is maximally human-auditable -- every memory can be read, edited, and diffed
- Git compatibility enables version control of agent memory -- rollback, branch, blame, merge
- Knowledge graph from wiki-links is zero-infrastructure: no Neo4j, no Kuzu, just filesystem links
- Checkpointing provides crash safety: save state mid-session, restore on restart
- Local-first with no cloud means data sovereignty is guaranteed
- Hybrid search (BM25 + embeddings + RRF) validates the same approach Lyra uses with SQLite + BM25 + sqlite-vec
- Structured fact extraction at write-time (not just at consolidation) enables real-time knowledge graph updates

## Relevance for Lyra Memory

**Levels impacted**: Level 2 (episodic), Level 3 (semantic), consolidation pipeline.

This is the second ClawVault analysis in our collection (see `clawvault.md` for the deep dive). This source from The Turing Post adds external validation of ClawVault's approach and highlights specific patterns worth considering.

**Markdown vs. SQLite tradeoff**:

| Dimension | Markdown (ClawVault) | SQLite (Lyra) |
|-----------|---------------------|---------------|
| Human readability | Excellent -- files are plain text | Requires queries or tools |
| Search at scale | Degrades with file count | Constant-time via indexes |
| Hybrid search | External tool (qmd) | Built-in (BM25 + sqlite-vec) |
| Git versioning | Native | Requires export or WAL snapshots |
| Atomic writes | Filesystem (risk of partial writes) | ACID transactions |
| Cross-referencing | Wiki-links (implicit graph) | Foreign keys + JOINs |
| Concurrent access | File locks (fragile) | WAL mode (robust) |

Lyra's SQLite choice remains correct for our needs: hybrid search at scale, ACID guarantees for the consolidation pipeline, and concurrent access from multiple agents. But ClawVault's human-auditability advantage is real.

**Bridging the gap**: Lyra can provide human-auditability through an **export/snapshot** mechanism that dumps memory state to Markdown files on demand or as part of the nightly batch. This gives the best of both worlds: SQLite for operations, Markdown for auditing.

The checkpointing pattern is directly applicable. Our Level 1 session memory currently has no explicit checkpoint mechanism. If the hub process crashes mid-session, the session state is lost. Adding periodic checkpoints (e.g., after every N messages or every M minutes) to the session's `aiosqlite` record would provide crash recovery.

## Applicable Patterns

- **Session checkpointing** (Level 1): Every N messages (e.g., 5) or M minutes (e.g., 10), checkpoint the current session state to `aiosqlite`. On pool restoration after crash, load the last checkpoint. Schema addition: `session_checkpoints` table with `pool_id, checkpoint_data (JSON), created_at`.
- **Markdown export for auditability** (Levels 2-3): Provide a `lyra export-memory` command that dumps all episodic entries and semantic facts to a Markdown directory structure, git-compatible. Run as part of the nightly batch or on demand. This addresses the transparency requirement without sacrificing SQLite's operational advantages.
- **Structured fact extraction at write-time** (Level 3): Instead of waiting for the nightly batch to extract facts from episodes, extract simple facts (entities, dates, explicit statements) at write-time during the session. The nightly batch then handles complex facts (inferences, promotions, contradictions).
- **Wake/sleep lifecycle** (Level 1): Formalize pool lifecycle as: `wake` (load checkpoint + query semantic facts) -> `process` (handle messages, checkpoint periodically) -> `sleep` (flush to episodic, generate handoff summary). This mirrors ClawVault's lifecycle but uses SQLite instead of files.
- **Crash recovery protocol** (Level 1): On hub restart, scan for pools with checkpoints but no `sleep` event. These are crash victims. Restore from last checkpoint, log a warning, continue. No data loss.

## Priority

**Phase 1**: Session checkpointing should be implemented as part of the pool lifecycle. It is low effort (one additional table, periodic write) and high value (crash recovery). The wake/sleep lifecycle formalization is also Phase 1 -- it structures the pool management code that must be written anyway.

**Phase 2**: Markdown export for auditability. Not urgent for Phase 1 (single user, low volume), but valuable when memory grows and debugging becomes harder.

**Phase 2**: Write-time fact extraction as a complement to the nightly batch. Requires the memory SLM or at least a heuristic extractor.
