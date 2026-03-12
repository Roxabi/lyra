# Agentic Patterns Catalog

> Source: https://agentic-patterns.com/
> Tier: 2 (Reference)
> KB ID: mem-patterns-catalog-008

## Summary

Agentic Patterns is a curated collection of production-ready design patterns for AI agents, organized into 8 categories: Context & Memory, Feedback Loops, Orchestration, Reliability, Security, Tool Use, UX & Collaboration, and a general category. Each pattern is described as repeatable, agent-centric, and traceable. The catalog draws from real-world learnings at Sourcegraph.

The patterns are explicitly designed for production environments, not research prototypes. They address the practical challenges of building agents that work reliably: how to manage context windows, how to handle failures gracefully, how to coordinate multiple agents, how to prevent prompt injection, and how to design interactions that keep humans in control.

The Context & Memory category is the most directly relevant to Lyra's memory architecture. It likely contains patterns for context window management, memory retrieval strategies, consolidation approaches, and cache invalidation. The Reliability category is relevant for our consolidation pipeline (what happens if compaction fails mid-way? how do we ensure no data loss during memory transitions?).

## Key Insights

- 8 categories cover the full lifecycle of an agentic system -- memory is one of eight concerns, not the only one
- Production-ready means battle-tested at Sourcegraph scale -- not theoretical patterns
- Each pattern is repeatable (can be applied across systems), agent-centric (designed for autonomous agents), and traceable (auditable decision chain)
- Context & Memory is a dedicated category -- confirms that memory management is a recognized first-class concern
- Reliability patterns address failure modes in agent systems -- directly applicable to consolidation safety
- Security patterns address prompt injection and data exfiltration -- relevant for Level 3/4 where user data is stored persistently
- The catalog format (patterns as atomic, composable units) mirrors our own skill/procedure approach

## Relevance for Lyra Memory

**Levels impacted**: All levels, with emphasis on Level 0 (context management), Level 3 (retrieval patterns), and the consolidation pipeline.

The Context & Memory category should be studied in detail for patterns applicable to:
- **Context window management** (Level 0): How to decide what stays in context and what gets evicted. Token budgeting strategies. Priority-based context composition.
- **Memory retrieval** (Level 3): Patterns for when and how to query the semantic store. Pre-retrieval (at session start), mid-conversation retrieval (on demand), and post-retrieval filtering (relevance scoring).
- **Consolidation safety** (Levels 2->3): Patterns for ensuring atomicity of the episodic-to-semantic promotion. What happens if the nightly batch fails halfway? How to ensure no episodic data is lost before its semantic facts are confirmed written.

The Reliability category is critical for the consolidation pipeline:
- **Flush before compact** (already identified as a hard rule): The pattern of persisting to external memory before any context compaction
- **Idempotent consolidation**: The nightly batch should be safely re-runnable if it fails partway through
- **Checkpointing**: Save progress during long consolidation runs so partial work is not lost on failure

The Orchestration category becomes relevant in Phase 5 (multi-agent). The Security category is relevant for Level 3/4 where persistent user data creates privacy and injection risks.

## Applicable Patterns

- **Flush-before-compact** (Levels 1->2, consolidation): Before any context window compaction, flush all pending observations to episodic memory. This is already a hard rule in our design, but the catalog likely formalizes it as a named pattern.
- **Idempotent batch processing** (Level 3, consolidation): The nightly batch that promotes episodic facts to semantic must be idempotent. Use a `processed_at` timestamp on episodes to track which have been consolidated. Re-running the batch should skip already-processed episodes.
- **Priority-based context composition** (Level 0): When building the system prompt, rank all candidate context items (identity, rules, semantic facts, recent history) by priority and fill the token budget from highest to lowest. Cut at the budget limit.
- **Retrieval-augmented generation (RAG) timing** (Level 3): The catalog likely formalizes when to retrieve from memory: at session start, before tool calls (PreToolUse hook pattern from our KB), and on explicit "remember" triggers.
- **Audit trail for memory writes** (Levels 2-4): Every write to episodic, semantic, or procedural memory should be logged with: source, timestamp, agent, confidence. This creates an auditable chain for debugging memory quality.

## Priority

**Phase 1**: Study the Context & Memory and Reliability categories in detail. Extract 3-5 patterns directly applicable to the Level 0 token budget and Level 3 retrieval implementation. The flush-before-compact and idempotent batch patterns should be implemented from the start.

**Phase 2**: Study the Feedback Loops category when implementing the memory SLM. The SLM's learning loop (observe -> extract -> store -> retrieve -> reason) is a feedback loop pattern.

**Phase 3+**: Study Orchestration (for multi-agent), Security (for persistent memory protection), and UX (for human-in-the-loop approval flows).
