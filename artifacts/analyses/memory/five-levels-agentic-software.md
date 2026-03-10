# Five Levels of Agentic Software

> Source: https://x.com/ashpreetbedi/status/2024885969250394191
> Tier: 2 (Reference)
> KB ID: mem-five-levels-005

## Summary

Ashpreet Bedi outlines 5 progressive levels of agentic software complexity: (1) agent + tools, (2) agent + storage/knowledge, (3) agent + memory, (4) multi-agent systems, and (5) production systems. The key message is to start simple and add capabilities only when the previous level is stable.

Level 1 is a basic agent that can call tools (web search, code execution, APIs). Level 2 adds persistent storage and a knowledge base -- the agent can now reference documents and structured data. Level 3 introduces memory: the agent remembers past interactions, learns user preferences, and maintains context across sessions. Level 4 is multi-agent: multiple specialized agents coordinated by an orchestrator. Level 5 is the production system: monitoring, fallbacks, rate limiting, authentication, and all the operational concerns that make a system reliable.

Bedi uses the Agno framework for examples but the progression is framework-agnostic. The critical insight is the ordering: memory (Level 3) comes before multi-agent (Level 4), which comes before production hardening (Level 5). Trying to jump levels leads to fragile systems.

## Key Insights

- The 5-level progression is a maturity model, not a feature checklist -- each level must be stable before moving to the next
- Memory comes before multi-agent: an agent without memory cannot effectively participate in multi-agent coordination
- Storage/knowledge (Level 2) is distinct from memory (Level 3): one is reference data, the other is learned context
- Production concerns (Level 5) are always last -- premature optimization of reliability before the core works is a trap
- Starting with agent + tools (Level 1) forces you to validate the tool integration before adding complexity
- Multi-agent (Level 4) without stable memory (Level 3) leads to agents that forget their coordination context

## Relevance for Lyra Memory

**Levels impacted**: All levels, but primarily as a strategic validation.

This progression directly validates Lyra's phased roadmap:

| Bedi's Level | Lyra Phase | Status |
|-------------|------------|--------|
| 1. Agent + tools | Phase 1 (hub + adapters + command router) | Done |
| 2. Agent + storage | Phase 1b (vault as semantic backend, #78) | Active |
| 3. Agent + memory | Phase 1b (memory levels 0+3, #9) | Active |
| 4. Multi-agent | Phase 5 (multi-agent orchestration, #63) | Planned P3 |
| 5. Production | Phase 4 (resilience, observability, security, #62) | Planned P3 |

The alignment is nearly exact. The most important validation: our decision to implement memory (Levels 0+3) in Phase 1b, before multi-agent (Phase 5), is confirmed as the correct ordering. A single agent with good memory is more valuable than multiple agents without it.

The distinction between storage/knowledge (Level 2) and memory (Level 3) maps to our semantic store (Level 3, reference facts) vs. episodic+procedural memory (Levels 2+4, learned context). Phase 1 focuses on Level 3 because it provides the most immediate value: the agent can recall facts without the full consolidation pipeline.

One challenge this model surfaces: Lyra's Phase 4 (production hardening) is currently planned before Phase 5 (multi-agent), which contradicts Bedi's ordering. However, for a personal-use system, some production concerns (resilience, observability) are needed earlier to prevent data loss.

## Applicable Patterns

- **Level-gating** (Architecture): Do not start implementing Level N+1 features until Level N is validated with real usage. This means: no multi-agent work until the memory layer is stable and tested in daily use.
- **Memory before orchestration** (Roadmap): The memory layer (#9, #78) must be fully operational before any multi-agent orchestration work begins. This confirms our Phase 1b -> Phase 5 sequencing.
- **Storage vs. Memory distinction** (Levels 2-3): In the schema, separate "reference knowledge" (documents, links, external data indexed for retrieval) from "learned memory" (observations, preferences, patterns derived from interactions). They have different write patterns, different staleness characteristics, and different retrieval priorities.
- **Incremental capability addition** (All phases): Each phase should add exactly one major capability. Phase 1b adds memory. Phase 2 adds local LLM. Phase 5 adds multi-agent. Never two at once.

## Priority

**Phase 1 (validation)**: This analysis validates current roadmap decisions. No new implementation work needed -- it confirms we are on the right track.

**Phase 1b (action)**: Focus entirely on memory (Levels 0+3) before any multi-agent or SLM work. The temptation to jump to Phase 2 (SLMs) before memory is solid is the exact mistake Bedi warns against.

**Phase 4-5 (sequencing check)**: Re-evaluate whether some Phase 4 production concerns (data backup, crash recovery) should move earlier to protect the memory layer.
