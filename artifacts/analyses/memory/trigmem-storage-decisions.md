# TrigMem — Storage Decision Framework

> Source: https://lsmod.github.io/TrigMem/01-theory/
> Tier: 1 (Directly actionable)
> KB ID: mem-trigmem-007

## Summary

TrigMem proposes a theoretical framework for reasoning about AI agent memory storage: the "impossible triangle" between Context Economy (minimizing tokens), Precision (getting the right information), and Reusability (using the same memory across contexts). You can optimize for any two, but the third will suffer. This creates fundamental trade-offs that every memory system must navigate.

The framework provides a comparison matrix of storage mechanisms — CLAUDE.md, Rules files, Skills, Commands, Sub-agents — evaluated on 5 axes: portability (can it move between projects?), loading strategy (always loaded vs. on-demand), isolation (does it pollute other contexts?), precision (does it give exactly the right info?), and economy (how many tokens does it cost?). Each mechanism occupies a different position in the triangle, and no single mechanism is optimal for all use cases.

The key insight is that the storage decision should be driven by the triangle position, not by implementation convenience. If you need high precision + high reusability, you sacrifice economy (large context). If you need high economy + high precision, you sacrifice reusability (highly specific, non-portable memories). Understanding these trade-offs prevents building a memory system that accidentally optimizes for the wrong vertex.

## Key Insights

- **Impossible triangle**: Context Economy vs. Precision vs. Reusability — pick two
- Storage mechanisms are not interchangeable — each has a distinct trade-off profile
- Always-loaded memory (high reusability) is expensive (low economy) but precise
- On-demand retrieval (high economy) requires good search (precision at risk)
- Portable/reusable memories tend to be generic (lower precision for specific tasks)
- The 5-axis comparison (portability, loading, isolation, precision, economy) is a practical evaluation framework
- Sub-agents as memory stores offer high isolation but high latency
- The framework is tool-agnostic — it applies to any system, not just Claude

## Relevance for Lyra Memory

**Levels impacted**: All levels — this framework guides what each level should store and how.

The TrigMem triangle maps onto Lyra's 5 levels as design guidance:

| Lyra Level | Triangle Position | Optimizes For | Sacrifices |
|------------|------------------|---------------|------------|
| L0 Working | Economy + Precision | Minimal, exact context for this turn | Reusability (turn-specific) |
| L1 Session | Precision + Reusability | Complete session state | Economy (grows over session) |
| L2 Episodic | Precision + Reusability | Dated, searchable archives | Economy (storage cost) |
| L3 Semantic | Economy + Reusability | Atomic facts, cross-context | Precision (may retrieve wrong facts) |
| L4 Procedural | Reusability + Precision | Stable, precise procedures | Economy (always loaded or large) |

Key observations from this mapping:
- L3 (semantic) is our "high economy + high reusability" layer, which means precision is the risk. This justifies investing in retrieval quality (BM25 tuning, Phase 2 reranking).
- L0 (working) should be aggressively economical — never load more than needed.
- L4 (procedural) is "high reusability + high precision" — it is expensive to maintain but worth it because procedures are used repeatedly.

## Actionable Items

- **[Architecture, Phase 1]** Use the impossible triangle to evaluate every memory design decision. When adding a new memory feature, explicitly state which triangle vertex is being sacrificed and why.
- **[Level 3, Phase 1]** Accept that L3 precision is the weak point. Mitigate with: relevance thresholds on retrieval, top-K limits, and Phase 2 reranking. Do not try to make L3 perfectly precise — the triangle says that is impossible at our economy/reusability targets.
- **[Level 0, Phase 1]** Budget L0 strictly. Define a hard token limit for working memory (e.g., 2,000 tokens for system prompt + injected memories). Any memory injection that exceeds this budget must be rejected.
- **[Level 4, Phase 2]** Accept that L4 is expensive. Procedural knowledge that is loaded on-demand (like skills) should be few and high-quality. Avoid creating many small procedures — they dilute precision.
- **[5-Axis Evaluation, Phase 1]** Before implementing each level, evaluate it on the 5 TrigMem axes (portability, loading, isolation, precision, economy). Document the evaluation in the spec.
- **[Level 3, Phase 2]** The precision gap in L3 is the primary argument for the memory SLM. The SLM adds precision (reasoning over facts) at the cost of economy (SLM inference cost). This is a deliberate triangle shift.

## Priority

**Phase 1 (design tool)**: Use the framework during architecture and spec writing. It does not require implementation — it is a decision-making tool. Every memory-related design decision in Phase 1 should reference the triangle trade-off explicitly.
