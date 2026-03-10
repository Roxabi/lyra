# Living Memory — Tulving's 3 Memory Systems

> Source: https://x.com/molt_cornelius/status/2025408304957018363
> Tier: 1 (Directly actionable)
> KB ID: mem-tulving-002

## Summary

Cornelius proposes a memory architecture for AI agents directly inspired by Endel Tulving's taxonomy of human memory. The system defines three distinct memory spaces: semantic (a knowledge graph of general, durable facts), episodic (a self-space of dated, contextualized experiences), and procedural (methodology — know-how and workflows). Each space has its own characteristics and purpose, mirroring how human cognition separates "knowing that", "remembering when", and "knowing how".

The critical contribution is the concept of **metabolic rate** — each memory type processes and changes at a different speed. Semantic memory is slow-metabolic: facts change rarely and persist for long periods. Episodic memory is medium-metabolic: events are recorded frequently but decay over time. Procedural memory is slow-metabolic but different from semantic: methods evolve gradually through practice and refinement.

The flows between memory spaces are **directional**, like a digestive system transforming raw information into knowledge. Raw input enters as episodic memory (experience), gets digested into semantic facts (understanding), and crystallizes into procedural knowledge (competence). Information flows one way through this pipeline — you don't "un-learn" a fact back into an episode.

## Key Insights

- Three memory types map directly to Lyra's levels 2/3/4: episodic, semantic, procedural
- Metabolic rate is the key to consolidation scheduling — each level consolidates at a different frequency
- Directional flow means consolidation is a pipeline, not bidirectional: episodic -> semantic -> procedural
- Semantic memory is the "knowledge graph" — general truths independent of when they were learned
- Episodic memory is always dated and contextualized — the "when and where" matters
- Procedural memory is implicit — it's about how to do things, not declarative knowledge
- The digestive metaphor implies that raw information must be processed (transformed) before it becomes knowledge — it is not just moved, it is distilled

## Relevance for Lyra Memory

**Levels impacted**: All levels (0-4), but primarily the consolidation pipeline between levels 2, 3, and 4.

This IS our architecture's theoretical foundation. The 5-level Lyra system extends Tulving's 3-level model with working memory (Level 0) and session memory (Level 1) as pre-episodic stages. The mapping is direct:

| Tulving | Lyra Level | Metabolic Rate |
|---------|------------|----------------|
| (pre-memory) | L0 Working | Instant (per-turn) |
| (pre-memory) | L1 Session | Fast (per-session) |
| Episodic | L2 Episodic | Medium (daily) |
| Semantic | L3 Semantic | Slow (weekly/nightly) |
| Procedural | L4 Procedural | Very slow (monthly) |

The metabolic rate concept directly determines consolidation frequency:
- L0 -> L1: every turn (append)
- L1 -> L2: end of session (summarize)
- L2 -> L3: nightly batch (extract facts)
- L3 -> L4: manual or monthly (crystallize procedures)

## Actionable Items

- **[Consolidation, Phase 1]** Implement the directional pipeline: L1 -> L2 at session end, L2 -> L3 in nightly batch. Never skip levels (don't go L1 -> L3 directly).
- **[Level 2, Phase 1]** Episodic entries must always have temporal context: `timestamp_start`, `timestamp_end`, `channel`. The "when and where" is what makes them episodic.
- **[Level 3, Phase 1]** Semantic facts must be temporally independent — strip the "when" during L2 -> L3 promotion. "On March 2, Mickael said he prefers Python" (episodic) becomes "Mickael prefers Python" (semantic).
- **[Scheduler, Phase 1]** Set consolidation frequencies based on metabolic rate: session-end for L1->L2, nightly for L2->L3. Do NOT consolidate L2->L3 in real-time.
- **[Level 4, Phase 2]** Procedural memory should only form from multiple semantic facts converging on a pattern. This is the slowest metabolic rate — monthly review at most.
- **[Architecture, Phase 1]** Document the Tulving mapping in the codebase. Every developer touching memory must understand why the levels exist and why they consolidate at different rates.

## Priority

**Phase 1 (core)**: This is the foundational model. The consolidation pipeline, level definitions, and metabolic rate scheduling are all Phase 1 decisions that flow directly from this framework. Already captured in `02-memory.md` but the metabolic rate scheduling must be made explicit in the implementation spec.
