# ClawVault — Malleable Primitives

> Source: https://x.com/sillydarket/status/2023232371038757328
> Tier: 1 (Directly actionable)
> KB ID: mem-clawvault-009

## Summary

ClawVault v2.6 introduces a system of composable memory primitives: tasks, projects, decisions, lessons, and people. Each primitive is stored as a Markdown file with YAML frontmatter containing structured metadata (type, status, tags, relationships). The primitives are intentionally malleable — the agent can modify not just the content of its memories, but the schemas and templates that define how memories are structured. Over time, the agent evolves its own organizational system based on what it learns about effective knowledge management.

The system uses trigger-based autonomy rather than scheduled jobs. Instead of running consolidation on a fixed cron schedule, certain events trigger specific memory operations: completing a task triggers a lessons-learned extraction, a new project triggers a decision-log initialization, encountering a recurring pattern triggers a procedure creation. This event-driven approach is more responsive than cron-based scheduling and avoids the wasted cycles of polling for changes.

The combination of malleable schemas and trigger-based processing creates a self-improving memory system. Early on, the agent uses simple templates (title + body). Over weeks of usage, the templates grow more sophisticated as the agent discovers which metadata fields are actually useful and which are noise. This organic evolution is more robust than a pre-designed schema because it adapts to actual usage patterns.

## Key Insights

- Composable primitives (tasks, projects, decisions, lessons, people) provide a structured vocabulary for memory types
- YAML frontmatter enables structured queries over unstructured content — best of both worlds
- Malleable templates: the agent modifies its own schemas over time, adapting to actual usage
- Trigger-based processing is more responsive and less wasteful than cron-based scheduling
- Schema evolution is organic: start simple, grow complexity based on what proves useful
- The system is self-improving: the agent gets better at organizing knowledge as it learns what works
- Primitives are composable: a decision can reference a project, which references people, forming a natural graph

## Relevance for Lyra Memory

**Levels impacted**: Level 4 (procedural), and the consolidation trigger design.

ClawVault's malleable primitives are directly relevant to Level 4 (procedural memory). Our current L4 design stores procedures as static Markdown files with a version counter. ClawVault shows that procedures should be self-evolving: Lyra should be able to refine its own procedures based on outcomes.

The trigger-based approach offers a complement to our nightly batch:

| Consolidation Type | Trigger | Lyra Implementation |
|---|---|---|
| Routine (L2 -> L3) | Nightly batch | Cron job, 3 AM |
| Critical (L0 -> L1) | Every message | Event-driven, mandatory |
| Urgent (L1 -> L2) | Session end | Event-driven, mandatory |
| Adaptive (L3 -> L4) | Pattern detection | Trigger: N concordant facts about same entity |

The key insight is that not all consolidation should be on the same schedule. Some events (session end, critical pattern detection) justify immediate consolidation, while routine fact extraction can wait for the nightly batch. This is not the same as the "real-time indexing" that caused crashes (mem-reindex-006) — these are lightweight, targeted writes, not full re-indexing operations.

## Actionable Items

- **[Level 4 Schema, Phase 2]** Design procedural memory entries with YAML frontmatter: `type`, `version`, `created_at`, `updated_at`, `trigger_conditions`, `effectiveness_score`. Allow the system to track which procedures are actually used and effective.
- **[Level 4, Phase 2]** Implement procedure evolution: when a procedure is used and the outcome is suboptimal, create a new version (increment `version`) with modifications. Keep old versions for rollback. Never mutate in place.
- **[Consolidation, Phase 1]** Implement a hybrid trigger system: nightly batch for routine L2 -> L3 consolidation, plus event-driven triggers for: (a) session end -> L1 -> L2, (b) pre-compaction -> L0 -> L1 flush. Keep it simple — only these two event triggers in Phase 1.
- **[Level 4, Phase 3]** Schema evolution: allow Lyra to propose modifications to its own L4 templates. Require user approval for schema changes (safety constraint). Log all schema changes with rationale.
- **[Primitives, Phase 2]** Define Lyra's memory primitive types: `fact` (L3), `episode` (L2), `procedure` (L4), `preference` (L4), `entity` (L3). Each type has a defined schema but can evolve over time.
- **[Trigger System, Phase 1]** Implement triggers as simple event handlers in the hub's message pipeline: `on_session_end`, `on_pre_compaction`. These are the only two triggers needed for Phase 1. Phase 2 adds `on_pattern_detected`.

## Priority

**Phase 1 (partial)**: Event-driven triggers for session end and pre-compaction are Phase 1 (they are already mandatory from the three-failure-modes analysis). The malleable schema and procedure evolution are Phase 2-3.
**Phase 2**: Memory primitive types with structured frontmatter, procedure versioning.
**Phase 3**: Self-evolving schemas with user approval gate.
