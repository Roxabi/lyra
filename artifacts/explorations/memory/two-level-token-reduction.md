# Two-Level Token Reduction (Polymarket Agent)

> Source: https://x.com/lunarresearcher/status/2028122076616200233
> Tier: 1 (Directly actionable)
> KB ID: mem-tokenredux-003

## Summary

A Polymarket prediction agent achieved a 67% reduction in token usage per request (8,200 to 2,700 tokens) and a proportional cost reduction from $73 to $24 per day. The technique is architecturally simple: split context into two tiers. The first tier is a "critical bootstrap" — a minimal, always-loaded prompt that contains only the essential instructions and identity the agent needs for every single request. The second tier is a MEMORY.md-style store with semantic search, loaded on demand based on the current request.

The key insight is that most memory is irrelevant to most requests. Loading everything into context is wasteful and potentially harmful (more noise = lower signal quality). By making the second tier queryable rather than always-present, the agent only pays the token cost for memories that are actually relevant to the current task.

The 67% reduction is not just a cost optimization — it also improves response quality. Smaller, more focused context windows lead to better attention distribution across the tokens that matter. The agent is not distracted by irrelevant past information.

## Key Insights

- Two tiers: always-loaded bootstrap (identity + core instructions) vs. on-demand semantic retrieval
- 67% token reduction = 67% cost reduction with no quality loss (quality actually improved)
- The retrieval layer must be queryable — the system formulates a query from the current request and retrieves only matching memories
- "Load everything" is an anti-pattern: more context != better performance
- The bootstrap tier should be aggressively minimal — only what is needed for EVERY request
- This is a production-validated pattern at scale (real money on Polymarket)

## Relevance for Lyra Memory

**Levels impacted**: Level 0 (working memory), Level 3 (semantic retrieval).

This validates our Phase 1 decision to implement only levels 0 + 3. The Polymarket pattern maps directly:

| Polymarket | Lyra |
|------------|------|
| Critical bootstrap | Level 0 (working memory — system prompt, identity, core instructions) |
| MEMORY.md + semantic search | Level 3 (semantic — BM25 + sqlite-vec, queried per request) |

The intermediate levels (1, 2) are not needed for the core retrieval loop. They exist for the consolidation pipeline (session -> episodic -> semantic), but at query time, it's always L0 + L3.

This also informs the retrieval API design: the hub must formulate a query from the incoming message, search Level 3, and inject only the top-K results into the context window. Never dump the entire semantic store.

## Actionable Items

- **[Level 0, Phase 1]** Define and maintain a minimal bootstrap prompt. Audit it regularly — every token in the bootstrap must justify its presence. Target: under 1,000 tokens.
- **[Level 3, Phase 1]** Implement retrieval as a query, not a load. The hub extracts keywords/intent from the incoming message, runs BM25 against `semantic_facts`, and injects only the top-K results (K=5 to start, tunable).
- **[Retrieval API, Phase 1]** Design the retrieval function signature: `async def retrieve_relevant_memory(user_id: str, query: str, top_k: int = 5) -> list[SemanticFact]`. This is the core API that Level 0 calls to populate context.
- **[Monitoring, Phase 1]** Track tokens per request before and after memory injection. Set a budget: memory injection should not exceed 30% of total context window.
- **[Level 3, Phase 1]** Implement a relevance threshold — do not inject memories with BM25 score below a minimum. Better to inject nothing than to inject noise.

## Priority

**Phase 1 (core)**: This is the retrieval pattern for Phase 1. The two-level architecture (bootstrap + semantic search) is exactly what we implement. The retrieval API is a P0 deliverable.
