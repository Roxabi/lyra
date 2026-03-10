# 13 Agents with Shared Brain

> Source: https://x.com/vadimstrizheus/status/2029200944223908025
> Tier: 2 (Reference)
> KB ID: mem-shared-brain-003

## Summary

Vadim Strizheus (18 years old) runs 13 AI agents for his solo business. Each agent has a well-defined identity stored as a `.md` file, daily memory logs, and access to a shared brain consisting of 21 JSON files. A spawn script injects the agent's identity, its daily memory, and the shared brain at launch time. This ensures every agent starts with full context and a consistent world model.

The infrastructure uses 8 SQLite databases combined with vector knowledge bases. A Telegram approval pipeline serves as the human-in-the-loop mechanism: agents propose actions, Vadim approves or rejects via Telegram, and the outcome feeds back into the shared brain. This creates a closed feedback loop where human judgment refines agent behavior over time.

The 21 JSON files in the shared brain contain business rules, project status, client information, and operational procedures. They act as the single source of truth across all 13 agents. When one agent learns something (e.g., a new client preference), it updates the shared brain, and the next agent spawn picks up the change. The daily memory is per-agent and captures session-specific observations.

## Key Insights

- Spawn script pattern: identity + memory + shared brain injected at every launch -- no persistent state in the agent process itself
- Shared brain (21 JSON files) acts as the canonical source of truth across agents -- analogous to a distributed knowledge base
- Per-agent daily memory prevents cross-contamination of context between different agent roles
- Telegram approval pipeline provides lightweight human-in-the-loop for critical decisions
- 8 SQLite DBs suggest domain-specific partitioning of data (not one monolithic database)
- The 18-year-old running 13 agents solo validates that the architecture does not need to be complex to be effective
- Identity files (.md) are immutable config -- the agent never modifies its own identity

## Relevance for Lyra Memory

**Levels impacted**: Level 0 (working), Level 1 (session), Level 3 (semantic), Level 4 (procedural).

The spawn script pattern maps directly to Lyra's agent initialization flow. In Lyra, when a binding resolves and `get_or_create_pool()` fires, the equivalent of the spawn script is:

1. Load agent identity (immutable system prompt + persona) -- Vadim's `.md` identity
2. Load pool session state (Level 1) -- Vadim's daily memory
3. Query semantic memory for relevant facts (Level 3) -- Vadim's shared brain (21 JSON)

The shared brain concept is particularly interesting. Lyra's Level 3 semantic store is namespace-isolated per agent (`memory_namespace` field on Agent). But Vadim's architecture shows that some facts must be shared across agents. This suggests Lyra needs a `shared` namespace in the semantic store that all agents can query.

The Telegram approval pipeline validates our human-in-the-loop approach for Level 4 (procedural) updates. When the system wants to promote a fact to a durable procedure, requiring human approval prevents hallucinated procedures from becoming permanent.

## Applicable Patterns

- **Spawn-time context injection** (Levels 0+3): At pool creation, inject: (a) agent identity/system prompt, (b) recent session state, (c) relevant semantic facts. This is the "spawn script" for Lyra.
- **Shared namespace in semantic store** (Level 3): Add a `shared` memory namespace accessible by all agents. Business rules, user preferences, cross-cutting facts live here. Per-agent namespaces hold role-specific knowledge.
- **Human-in-the-loop for procedural promotion** (Level 4): When the consolidation process identifies a candidate for Level 4 promotion, send an approval request via the channel adapter (e.g., Telegram). Only promote on explicit approval.
- **Identity immutability** (Agent model): Validates our decision that Agent is a stateless singleton with immutable config. The identity `.md` file is never modified by the agent itself.
- **Domain-partitioned storage** (Level 3): Vadim's 8 SQLite DBs suggest that at scale, a single `memory.db` may need partitioning. For Phase 1, a single DB is fine, but schema should support namespace-based sharding later.

## Priority

**Phase 1**: Spawn-time context injection is a core Phase 1 deliverable. The shared namespace concept should be designed into the schema now (a `namespace` column on `semantic_facts` with a reserved `shared` value), even if multi-agent is Phase 5.

**Phase 2**: Human-in-the-loop for procedural promotion can be deferred -- Phase 1 has no Level 4.

**Phase 5**: Full multi-agent with shared brain becomes relevant when orchestration is implemented.
