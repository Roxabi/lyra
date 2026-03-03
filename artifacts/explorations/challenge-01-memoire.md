# Challenge #1 — Memory: 5 Levels vs 2 Levels

> Challenge document based on knowledge base data.
> Last updated: 2026-03-02

---

## Our current plan

5-level memory architecture:
- Level 0: Working memory (active context window)
- Level 1: Session memory (multi-turn state per pool)
- Level 2: Episodic (dated Markdown, immutable)
- Level 3: Semantic (SQLite + BM25 + embeddings)
- Level 4: Procedural (learned skills, preferences)

---

## The challenge: field report -67% tokens

**Source**: [lunarresearcher, Twitter](https://x.com/lunarresearcher/status/2028122076616200233)

A Polymarket research agent reduced its costs by 67% by switching from a full context loaded on every request to a **2-level system**:

- Critical bootstrap (always in context)
- MEMORY.md + semantic search (loaded on demand)

**Real numbers**: 8,200 -> 2,700 tokens/request. $73 -> $24/day.

---

## What this calls into question

### 1. Is the 5-level complexity justified from the start?

The field report suggests that a 2-level system (critical + semantic) already covers 90% of cases at a fraction of the development cost.

Our levels 1 (session), 2 (episodic), 4 (procedural) are useful refinements — but not foundations.

**Risk**: over-architecting memory in Phase 1 slows down the MVP with no measurable benefit.

### 2. Prompt caching changes the equation

**Source**: [koylanai, Twitter](https://x.com/koylanai/status/2027819266972782633)

6 insights on Claude prompt caching:
- Put static content **before** dynamic content
- Cache hit rate = production metric (like uptime)
- Never modify tools mid-session (invalidates the cache)
- Inject updates via messages, not via system prompt

Our current architecture does not model prompt caching as a memory layer. Yet, with a well-configured cache, level 0 (working memory) becomes much more efficient.

### 3. Context engineering rather than monolithic memory

**Source**: [Trajectory Engineering video](https://www.youtube.com/watch?v=r15w8GT44WA)

3 levels of context mastery identified:
- Vibe-coder (90%): loads the entire context
- Intentional developer (9%): manages the context
- Trajectory engineer (1%): treats context as a tree to prune

Our level 2 (episodic, dated Markdown) corresponds exactly to this "contextual tree pruning" pattern.

---

## Recommendations

### Short term (Phase 1)
- **Implement levels 0 + 3 only** (working + semantic)
- Add prompt caching from the start as a cross-cutting optimization
- Measure actual tokens/request on Telegram traffic before adding levels

### Medium term (Phase 2)
- Add level 1 (session) when multi-turn commands become frequent
- Add level 2 (episodic) for auditability if it proves to be a real value
- Level 4 (procedural) = last because it is the most complex to validate

### Decision to make
**Is procedural level 4 really necessary?** The Polymarket experience shows that good BM25/embedding is sufficient to retrieve patterns. "Procedural memory" can be simulated by structured MEMORY.md files.

---

## Verdict

The 5-level complexity is intellectually elegant but may be a perfectionism trap. Start simple (2 levels), measure, then add. The empirical evidence (-67% tokens with 2 levels) speaks for itself.
