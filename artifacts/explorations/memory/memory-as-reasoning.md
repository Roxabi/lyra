# Memory as Reasoning

> Source: https://blog.plasticlabs.ai/blog/Memory-as-Reasoning
> Tier: 1 (Directly actionable)
> KB ID: mem-reasoning-001

## Summary

Plastic Labs proposes a paradigm shift: treating AI memory not as static storage and retrieval, but as a dynamic reasoning task. Traditional memory systems rely on vector databases to find the "closest" past interaction, which fundamentally limits what an agent can learn about a user. Instead, LLMs can apply deduction, induction, and abduction to build composable, updatable identity representations that go far beyond similarity search.

The core insight is to reframe the memory question from "what is the closest stored memory?" to "what is true about this user given all these observations?" This transforms memory from a lookup table into a reasoning engine. The system uses explicit reasoning traces as memory scaffolding — atomic, composable conclusions (e.g., "user prefers Python", "user values directness") that can be recombined dynamically based on context.

Each memory trace is an atomic conclusion derived from conversation history. These traces are composable (can be combined to infer higher-order facts), updatable (new evidence can revise or strengthen them), and context-dependent (the same traces can produce different inferences depending on the current query). This makes the memory system fundamentally more powerful than cosine similarity over embeddings.

## Key Insights

- Memory-as-reasoning treats stored facts as premises for inference, not endpoints for retrieval
- Atomic conclusions are composable: "prefers Python" + "builds CLIs" + "avoids servers" => "CLI-first Python developer" — emergent understanding
- Reasoning traces create an auditable chain: every conclusion links back to source observations
- Vector DBs answer "what is similar?" but reasoning answers "what is true?" — fundamentally different question
- The approach naturally handles contradictions: newer observations can update or override older conclusions through explicit reasoning
- Identity representation is dynamic and evolving, not a frozen snapshot

## Relevance for Lyra Memory

**Levels impacted**: Level 3 (semantic), Level 4 (procedural), and the Level 3→4 promotion logic.

This article is the philosophical foundation for how our memory SLM should work in Phase 2. Currently, Level 3 is BM25 + sqlite-vec (retrieval-based). Memory-as-reasoning says that retrieval alone is insufficient — the system must reason over retrieved facts to synthesize understanding.

Concretely, this changes the semantic layer from a pure storage/retrieval system into a reasoning substrate:
- **Level 3 stores atomic facts** (observations, conclusions with source links)
- **The memory SLM reasons over Level 3** to answer queries, not just retrieves
- **Level 3→4 promotion** happens when reasoning produces stable, reusable conclusions (procedures)

This validates our decision to have a dedicated memory SLM rather than just a search index. The SLM is not a luxury — it is the mechanism that transforms storage into understanding.

## Actionable Items

- **[Level 3, Phase 1]** Store semantic facts as atomic conclusions with explicit source references (`source_episode_ids` in schema). This lays the groundwork for reasoning traces even before the SLM exists.
- **[Level 3, Phase 1]** Add a `reasoning_chain` TEXT field to `semantic_facts` — even if Phase 1 populates it manually, Phase 2 SLM will use it.
- **[Level 3→4, Phase 2]** Implement the memory SLM as a reasoner, not a retriever. Input: query + retrieved facts. Output: synthesized conclusion with reasoning trace.
- **[Level 4, Phase 2]** Promotion from semantic to procedural should require the SLM to produce a stable conclusion across multiple reasoning passes (not just 3 mentions — 3 concordant *reasoned* conclusions).
- **[Level 3, Phase 2]** Implement contradiction detection: when a new fact conflicts with an existing one, the SLM must explicitly reason about which to keep, update, or mark as context-dependent.

## Priority

**Phase 1 (partial)**: Schema design must anticipate reasoning traces. Store atomic facts with sources now.
**Phase 2 (full)**: Memory SLM implementation is where this becomes fully operational. This is the core design document for the SLM's behavior — it should reason, not just search.
