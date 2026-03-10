# Three Failure Modes of Agent Memory

> Source: https://x.com/ksimback/status/2024180197910864182
> Tier: 1 (Directly actionable)
> KB ID: mem-failures-004

## Summary

This post identifies three recurring, critical failure modes in AI agent memory systems. The first is "memory never saved" — the model autonomously decides what to remember, and frequently decides nothing is worth saving. The result is an agent that forgets everything between sessions. The second is "saved but never retrieved" — memories exist in storage but the agent answers from its current context window, never searching its memory bank. The agent has knowledge but never uses it. The third is "destroyed by compaction" — context window compaction (summarization to reduce tokens) silently destroys information that was in working memory but never flushed to persistent storage.

Each failure mode has a corresponding architectural fix. For the first: never let the model decide what to save — implement explicit, mandatory writes triggered by system logic. For the second: make retrieval mandatory at session start and before tool calls — the agent must search before answering. For the third: mandatory flush to persistent storage before any compaction event — compaction must never destroy unflushed state.

These are not edge cases. They are the three most common ways agent memory systems fail in production, and all three can co-exist silently. An agent can appear functional while losing all long-term learning capability.

## Key Insights

- **Failure 1 (never saved)**: Models are bad at deciding what to save. System-level saves must be mandatory, not model-discretionary.
- **Failure 2 (never retrieved)**: An agent with perfect memory but no retrieval habit is functionally amnesiac. Retrieval must be forced, not optional.
- **Failure 3 (destroyed by compaction)**: Compaction is the silent killer. It looks like optimization but it destroys state. Flush before compact, always.
- These three failures are independent — fixing one does not fix the others
- All three can be prevented with hard architectural rules, not LLM prompting
- The solutions are not suggestions — they are non-negotiable constraints

## Relevance for Lyra Memory

**Levels impacted**: All levels. These are cross-cutting architectural constraints that must be enforced from Phase 1.

These three failure modes map directly to hard rules in Lyra's architecture:

| Failure Mode | Lyra Hard Rule | Level |
|---|---|---|
| Never saved | Explicit writes at session end (L1 -> L2). System triggers, not model decision. | L1, L2 |
| Never retrieved | Mandatory `retrieve_relevant_memory()` at session start AND before tool/skill calls | L0, L3 |
| Destroyed by compaction | Mandatory flush L0 -> L1 (append) and L1 -> L2 (summarize) before ANY compaction | L0, L1, L2 |

These rules are already noted in `02-memory.md` but must become enforced code constraints, not just documentation.

## Actionable Items

- **[Level 1→2, Phase 1]** Implement mandatory session-end consolidation. When a session ends (timeout, explicit close, or channel disconnect), the system MUST write L1 session log to L2 episodic. This is not optional. No "the model decides if it's worth saving".
- **[Level 0, Phase 1]** At every session start, the hub MUST call `retrieve_relevant_memory()` and inject results into the context. This is a mandatory step in the message processing pipeline, not a best-effort optimization.
- **[Compaction, Phase 1]** Implement a pre-compaction hook: before any context compaction, flush the current working memory to L1 (session log). Assert that the flush succeeded before allowing compaction to proceed. If flush fails, compaction must not run.
- **[Level 0→1, Phase 1]** Every message exchange must be appended to L1 session log before the response is sent. This ensures no information exists only in L0 (the context window).
- **[Testing, Phase 1]** Write integration tests for each failure mode:
  - Test 1: Verify that ending a session without explicit save still persists to L2
  - Test 2: Verify that a new session retrieves facts from L3 relevant to the first message
  - Test 3: Verify that compacting a long conversation does not lose facts that were in the pre-compaction context
- **[Architecture, Phase 1]** These three rules must be documented as NON-NEGOTIABLE constraints in the architecture spec. They are not optimizations — they are correctness requirements.

## Priority

**Phase 1 (mandatory)**: All three rules must be enforced from the very first implementation. These are not Phase 2 enhancements — they are Phase 1 correctness requirements. An agent without these rules will silently fail to learn, and the failure will be invisible until it is too late.
