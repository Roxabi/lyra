# System Prompt Bloat Reduction

> Source: https://x.com/code_rams/status/2025371800587436344
> Tier: 2 (Reference)
> KB ID: mem-prompt-bloat-007

## Summary

Ramya documents Day 5 of debugging her Chiti bot, where the root cause of degraded agent performance was system prompt bloat. The system prompt had grown to 11,887 tokens, including 51 skill declarations (20 unused) and a MEMORY.md of 200+ lines. The agent was spending most of its context window on static instructions rather than the actual conversation.

After cleanup: a boot sequence defined in an AGENTS.md file, a handover protocol for session transitions, and removal of all unused skills. The result: 8,529 tokens (-28% reduction), 32 skills (from 51), and noticeably improved response quality. The agent had more context window available for actual reasoning.

The key lesson is that context window is a finite, precious resource. Every token in the system prompt is a token not available for conversation history, retrieved memories, or reasoning. System prompt bloat is a silent performance killer -- the agent does not complain, it just gets dumber.

## Key Insights

- 11,887 tokens in a system prompt is dangerously high -- leaves too little room for actual conversation and memory retrieval
- 20 unused skills out of 51 means 39% dead weight -- skills must be loaded on demand, not declared upfront
- MEMORY.md at 200+ lines is a symptom of memory level confusion: boot context (Level 0) should be a summary, not a dump
- The -28% token reduction directly improved response quality -- context window budget is real
- Boot sequence (AGENTS.md) separates identity/instructions from memory -- correct architectural pattern
- Handover protocol ensures session transitions do not lose critical context
- Quantitative monitoring (token count) is essential -- bloat creeps in unnoticed without measurement

## Relevance for Lyra Memory

**Levels impacted**: Level 0 (working memory), Level 4 (procedural -- skill loading).

This is a critical warning for Level 0 design. Lyra's working memory is the context window, and every byte of injected context (system prompt, memory facts, skill declarations) competes with conversation history and reasoning space.

Concrete risks for Lyra:
- **Agent system prompt**: The persona, permissions, and instructions for an agent could easily balloon to thousands of tokens. Must be monitored.
- **Semantic fact injection**: When the pool is initialized and Level 3 facts are injected, how many facts? What is the token budget? Without a hard cap, the system will inject too many facts and crowd out the conversation.
- **Skill declarations**: If all skills are declared in the system prompt (like Chiti's 51 skills), the budget is consumed by capabilities the agent may never use in a given session.

The solution is a **token budget system** for Level 0:

| Component | Budget | Notes |
|-----------|--------|-------|
| Agent identity/persona | ~500 tokens | Fixed, immutable |
| Core instructions | ~1,000 tokens | Rules, constraints |
| Injected semantic facts | ~2,000 tokens | Top-N by relevance |
| Active skills | ~500 tokens | Only skills likely needed |
| **Total system prompt** | ~4,000 tokens | Hard cap |
| Conversation history | Remaining window | After system prompt |

## Applicable Patterns

- **Token budget for Level 0** (Level 0): Define a hard cap for the system prompt token count. Monitor it. Alert if it exceeds the threshold. This is a Phase 1 requirement -- without it, performance will silently degrade as more features are added.
- **Lazy skill loading** (Level 4 / skills): Do not declare all skills in the system prompt. Instead, declare a minimal set (5-10 most common), and dynamically inject additional skill declarations only when the routing SLM (Phase 2) or a keyword match suggests they are needed. This is the "32 skills from 51" optimization.
- **Boot context vs. full memory separation** (Levels 0 vs. 3): The system prompt should contain a compressed boot context (identity, key rules, top-5 most relevant facts). The full memory is in Level 3 and queried on demand during the conversation, not loaded upfront.
- **Token count monitoring** (Level 0): Add a `system_prompt_tokens` metric to the pool. Log it at session start. Track over time. Set up an alert if it exceeds the budget.
- **Periodic MEMORY.md audit** (Level 0): If a MEMORY.md-equivalent boot context is used, schedule periodic reviews to prune stale entries. This is the human equivalent of the nightly batch.

## Priority

**Phase 1 (critical)**: Token budget for Level 0 must be designed from the start. The system prompt builder should: (a) count tokens before injection, (b) truncate or prioritize if over budget, (c) log the actual token count. This prevents the silent degradation that Ramya experienced.

**Phase 1**: Lazy skill loading is relevant even before the SLM router exists. A simple heuristic (keyword matching on the user message) can select which skills to inject.

**Phase 2**: When the routing SLM is introduced, skill selection becomes intelligent rather than heuristic-based. But the budget system must already be in place.
