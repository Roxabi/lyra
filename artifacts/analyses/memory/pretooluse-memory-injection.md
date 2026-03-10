# PreToolUse Memory Injection

> Source: https://x.com/perceptualpeak/status/2016353615619961303
> Tier: 1 (Directly actionable)
> KB ID: mem-pretooluse-005

## Summary

A practitioner discovered that injecting semantic memory at the PreToolUse hook point (before each tool/skill invocation), rather than only at UserPromptSubmit (the initial user message), drastically improves agent effectiveness. The standard approach loads relevant memories once when the user sends a message, but tools and skills often introduce new context that changes what memories are relevant. By the time a tool is invoked, the conversation has evolved, and the initially retrieved memories may no longer be the most useful ones.

The PreToolUse injection pattern re-queries the memory system with the current state of the conversation (including the tool being called and its parameters) as the search query. This means each tool call gets its own set of relevant memories, tailored to the specific operation being performed. For example, if the user asks about their schedule and a calendar tool is about to be called, the memory system can inject preferences about scheduling, time zones, and meeting habits — even if these were not retrieved in the initial query about "my schedule".

This is a small architectural change with outsized impact: the retrieval query changes from "what does the user want?" to "what does the agent need to know to execute this specific tool call well?"

## Key Insights

- Retrieving memory only at message start is insufficient — context evolves during processing
- Each tool/skill call has its own memory needs, different from the initial query
- The retrieval query for PreToolUse should incorporate: the tool name, its parameters, and the conversation state
- This is not "more retrieval" — it is "better-timed retrieval". The total token cost may stay similar if initial retrieval is reduced.
- The pattern creates a feedback loop: tool context refines what memories are relevant, and those memories improve tool execution
- Cost can be managed by caching: if the retrieval query is similar to the initial one, reuse the results

## Relevance for Lyra Memory

**Levels impacted**: Level 0 (working memory composition), Level 3 (retrieval timing and API).

This changes the retrieval timing in Lyra's processing pipeline. Currently, the design assumes retrieval happens once at session/message start. This pattern says: retrieve again before each skill execution.

In Lyra's hub architecture, the processing pipeline looks like:

```
Message received
  -> [RETRIEVE L3] inject memories into context      (current design)
  -> Route to skill via CognitiveFrame
  -> Skill executes tool calls
     -> [RETRIEVE L3 AGAIN] inject skill-specific memories  (this pattern)
  -> Response generated
```

The hub's skill router must support a pre-execution hook where the retrieval function is called with the skill name + parameters as the query context. This is architecturally clean because the hub already controls the message pipeline.

## Actionable Items

- **[Hub Pipeline, Phase 1]** Add a pre-skill-execution hook in the message processing pipeline. Before dispatching to a skill, call `retrieve_relevant_memory()` with a query constructed from: `(user_message, skill_name, skill_parameters)`.
- **[Retrieval API, Phase 1]** Extend the retrieval function to accept optional context: `async def retrieve_relevant_memory(user_id: str, query: str, skill_context: Optional[str] = None, top_k: int = 5)`. The `skill_context` enriches the BM25 query.
- **[Performance, Phase 1]** Implement retrieval caching within a single message processing cycle. If the pre-skill query is similar to the initial query (e.g., cosine similarity > 0.9 on query embeddings, or shared BM25 terms), reuse cached results instead of re-querying.
- **[Level 0, Phase 1]** Define a maximum memory budget per message: initial retrieval + pre-skill retrieval combined must not exceed N tokens (e.g., 1,000). If pre-skill retrieval would exceed the budget, prioritize pre-skill results over initial results (they are more contextually relevant).
- **[Testing, Phase 2]** A/B test: compare response quality with initial-only retrieval vs. initial + pre-skill retrieval. Measure: task completion rate, user satisfaction, token cost.

## Priority

**Phase 1 (design)**: The hub pipeline must be designed with the pre-skill hook from the start, even if Phase 1 implements a simple version (same query, no skill context enrichment). Retrofitting this pattern later requires pipeline refactoring.
**Phase 2 (full)**: Skill-context-aware retrieval queries and caching optimization.
