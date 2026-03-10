# QMD vs Grep vs BM25 — Search Strategy Comparison

> Source: https://x.com/ArtemXTech/status/2028330693659332615
> Tier: 1 (Directly actionable)
> KB ID: mem-searchcomp-010

## Summary

This post provides a practical comparison of three search strategies for AI agent knowledge bases: grep (bruteforce text matching), BM25 (relevance-scored lexical search), and semantic search (meaning-based vector similarity). Grep is the simplest — it scans every file for exact or regex matches. For a 200-file knowledge base, this produces enormous amounts of noise because every file containing the search term is returned, regardless of relevance. It is fast for small corpora but does not scale and provides no ranking.

BM25 (Best Matching 25) is a relevance scoring algorithm that considers term frequency, document length, and inverse document frequency to rank results. It answers "which documents are most relevant to this query?" rather than "which documents contain this word?". The difference is dramatic: where grep returns 200 files of noise, BM25 returns 5-10 ranked results with the most relevant first. It is instant on typical knowledge bases and requires no GPU or embedding model.

Semantic search uses embeddings (vector representations of meaning) to find documents that are conceptually similar to the query, even if they share no words. "Python programming" can match "coding in snake language" if the embeddings capture the semantic relationship. This is the most powerful approach but requires an embedding model (GPU/API cost) and a vector database. QMD (Quick Markdown Database) is presented as a local search engine for markdown files that combines these approaches, installable as a skill in 2 minutes.

## Key Insights

- Grep: O(n) scan, no ranking, massive noise on 100+ file corpora — unsuitable for agent memory
- BM25: relevance-ranked, instant, no GPU needed, handles 95% of retrieval needs for structured knowledge
- Semantic search: meaning-based, handles synonyms and paraphrases, but requires embeddings (GPU/API cost)
- Hybrid (BM25 + semantic) provides the best of both: lexical precision + semantic coverage
- BM25 alone is sufficient for Phase 1 — it handles exact matches and term-based queries well
- Semantic search shines for vague/conceptual queries where the user does not know the exact terms
- QMD demonstrates that local-first search over markdown is viable and fast

## Relevance for Lyra Memory

**Levels impacted**: Level 3 (semantic storage and retrieval).

This comparison validates our Phase 1 and Phase 2 retrieval decisions:

| Phase | Retrieval Strategy | Rationale |
|-------|-------------------|-----------|
| Phase 1 | BM25 only | No GPU cost, instant, sufficient for fact-based queries |
| Phase 2 | BM25 + sqlite-vec (hybrid) | Adds semantic coverage for vague queries |
| Phase 2+ | Hybrid + SLM reranking | Adds reasoning-based precision |

We already have this stack working in 2ndBrain (`knowledge/memory.db` with SQLite + BM25 + sqlite-vec). The comparison confirms we are on the right path and provides clear justification for the phased approach.

Key validation points:
- BM25 is not a "lesser" approach — it handles most queries well and is the workhorse of information retrieval
- Semantic search is an enhancement, not a replacement — BM25 + semantic > semantic alone
- The hybrid approach (our Phase 2) is the industry best practice for small-to-medium knowledge bases
- Local-first (SQLite + sqlite-vec) is preferable to external vector DBs (Qdrant, Pinecone) for our use case — no network latency, no rate limits, no external dependency

## Actionable Items

- **[Level 3, Phase 1]** Implement BM25 search over `semantic_facts` table using SQLite FTS5. FTS5 has built-in BM25 ranking via `bm25()` function. Schema: create a virtual table `semantic_facts_fts USING fts5(fact, content=semantic_facts, content_rowid=rowid)`.
- **[Level 3, Phase 1]** Set retrieval parameters: top_k=5, minimum BM25 score threshold (tune empirically — start with score > 0 and adjust). Return results sorted by BM25 score descending.
- **[Level 3, Phase 2]** Add sqlite-vec column to `semantic_facts` for embedding storage. Use the same embedding model as 2ndBrain for consistency. Hybrid retrieval: `final_score = alpha * bm25_score + (1-alpha) * cosine_similarity`, with alpha tunable (start at 0.5).
- **[Level 3, Phase 2]** Implement reranking: after hybrid retrieval returns top-20 candidates, use the memory SLM to rerank based on contextual relevance to the current query. Return top-5 after reranking.
- **[Migration, Phase 1]** Study 2ndBrain's existing hybrid search implementation (`knowledge/memory.db`). Extract the BM25 + sqlite-vec patterns as a reusable library module for Lyra. Do not reimplement from scratch.
- **[Testing, Phase 1]** Create a retrieval benchmark: 50 test queries with expected results. Measure precision@5 and recall@5 for BM25-only. Use this benchmark to evaluate Phase 2 hybrid improvements.

## Priority

**Phase 1 (core)**: BM25 over FTS5 is a P0 deliverable. It is the retrieval engine for Level 3 and the foundation for all memory queries.
**Phase 2**: sqlite-vec embeddings, hybrid scoring, SLM reranking. Each is an incremental improvement on Phase 1's BM25 baseline.
