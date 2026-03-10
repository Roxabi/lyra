# Obsidian + Voyage AI Agent Memory Setup

> Source: https://x.com/yanndecoopman/status/2023657474994151769
> Tier: 2 (Reference)
> KB ID: mem-obsidian-voyage-004

## Summary

Yann De Coopman describes a setup combining an Obsidian vault of linked Markdown files with semantic indexing powered by Voyage AI (recommended by Anthropic for embeddings). Each agent produces standardized daily reports that feed into the vault. Knowledge propagation between agents happens automatically through the shared vault structure.

The architecture emphasizes linked documents: each Markdown file contains wiki-links to related files, creating an implicit knowledge graph. When Agent A discovers a fact relevant to Agent B's domain, it writes it to the vault with appropriate links, and Agent B picks it up on its next session start. This creates an organic, emergent knowledge propagation system without explicit inter-agent messaging.

A key operational rule is to never store credentials or secrets in the vault. The vault is designed to be human-readable, git-versionable, and potentially shared -- putting secrets in it would compromise all of those properties.

## Key Insights

- Voyage AI recommended by Anthropic for embeddings -- but this is a cloud service, not local-first
- Daily reports per agent create structured episodic memory with consistent format
- Wiki-links between documents create an implicit knowledge graph without explicit graph infrastructure
- Agent-to-agent knowledge propagation is passive (via shared vault) rather than active (via messaging)
- Obsidian vault structure is inherently human-auditable -- a key non-functional requirement
- Security rule: never store credentials in the knowledge store (applies to any memory system)
- Standardized report format enables automated parsing and summarization downstream

## Relevance for Lyra Memory

**Levels impacted**: Level 2 (episodic), Level 3 (semantic), and cross-agent communication.

The daily report pattern maps to our Level 2 episodic memory. Each agent interaction is logged as a dated, immutable record. The key improvement this suggests: standardize the episodic entry format. Instead of free-form summaries, each episode should follow a template (agent, channel, user, key facts learned, decisions made, open questions).

Agent-to-agent knowledge propagation via a shared store maps to cross-namespace queries in our Level 3 semantic store. Rather than agents sending messages to each other, Agent A writes a fact to the `shared` namespace, and Agent B picks it up when it queries at session start. This is simpler and more robust than direct inter-agent communication.

Regarding Voyage AI: we use local embeddings (nomic-embed-text via sentence-transformers). This is a deliberate choice -- local-first, no cloud dependency, no per-request cost. Voyage AI may produce better embeddings, but the privacy and cost tradeoffs are not acceptable for Lyra. The `fastembed` evaluation planned for Phase 2 remains our path.

The wiki-link implicit graph is interesting but does not apply to our SQLite-based approach. However, the concept of relational links between semantic facts is valuable. Our `source_episode_ids` JSON array on `semantic_facts` serves a similar purpose -- linking facts back to their source episodes.

## Applicable Patterns

- **Standardized episodic entry format** (Level 2): Define a fixed schema for episode records:
  ```
  agent, channel, user_id, timestamp_start, timestamp_end,
  summary, key_facts_extracted[], decisions_made[],
  open_questions[], raw_log
  ```
  This enables automated downstream processing (consolidation, search, reporting).

- **Passive cross-agent knowledge sharing** (Level 3): Agents write to a `shared` semantic namespace. Other agents pick up new facts on their next session init. No direct inter-agent messaging needed.

- **Security: no secrets in memory** (All levels): Hard rule for the memory layer: never store API keys, tokens, passwords, or credentials. Add a sanitization step before any write to episodic or semantic memory.

- **Embedding model evaluation** (Level 3, Phase 2): The Voyage AI mention is a reminder to benchmark embedding quality when evaluating `fastembed` vs `sentence-transformers` vs Ollama embeddings. Track: recall@10, latency, VRAM usage.

## Priority

**Phase 1**: Standardized episodic format should be part of the initial schema design. The security rule (no secrets in memory) should be a hard constraint in the memory writer. Cross-agent sharing is schema-level only (add `namespace` column).

**Phase 2**: Embedding model evaluation when `fastembed` or Ollama embeddings are tested. Cross-agent propagation becomes functional when multi-agent is implemented (Phase 5).
