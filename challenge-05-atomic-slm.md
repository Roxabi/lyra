# Challenge #5 — Atomic SLM: AI Routing vs Programmatic Tool Calling

> Challenge document based on knowledge base data.
> Last updated: 2026-03-02

---

## Our current plan (Phase 2)

4 specialized SLMs to replace the large LLM on routing tasks:

| Task | Size | Target latency |
|------|------|---------------|
| Routing / intent triage | ~1-3B | <50ms |
| Memory relevance scoring | ~1B | <30ms |
| Entity extraction (NER) | ~3B | <100ms |
| Skill selection / planner | ~3-7B | <200ms |

Expected impact: 80-90% of messages routed without the full LLM. Cost /10, latency /5.

---

## The challenge: Programmatic Tool Calling (PTC) — Claude Opus 4.6

**Source**: [@rlancemartin, Twitter](https://x.com/rlancemartin/status/2027450018513490419)

Lance Martin explains **Programmatic Tool Calling** in Claude Opus 4.6:

Instead of costly round-trips between each tool, Claude writes **Python code** that orchestrates tool calls in a container. Intermediate results stay in the code, not in the context.

**Results with Opus 4.6**:
- +11% accuracy on web search benchmarks
- -24% tokens
- 1st place on LMArena Search Arena

---

## What this calls into question

### 1. Are custom SLMs necessary?

Opus 4.6's PTC solves the same problem as our routing SLMs:
- Reduce tokens (PTC: -24%, our SLMs: -80-90%)
- Improve accuracy (PTC: +11%, our SLMs: latency <50ms)
- Avoid round-trips (PTC: Python code, our SLMs: structured pipeline)

**Key difference**: PTC runs with the large LLM (Opus 4.6), our SLMs run without it. For Lyra personal (low usage), PTC may be simpler to implement.

### 2. Cost changes the equation

Our custom SLMs:
- Machine 2 operational (P2)
- 4 models to maintain
- Finetuning or prompting on each SLM
- Significant development overhead

PTC with Anthropic API:
- A single model
- Higher latency (full LLM) but zero development overhead
- Per-token cost, not machine cost

**For Lyra personal (low volume)**: PTC may be more cost-effective than SLM infrastructure.

### 3. The cognitive meta-language (CognitiveFrame) remains relevant

Even if we abandon custom SLMs, the `CognitiveFrame` concept remains valid:
- Frames structure Lyra's reasoning independently of the model used
- A single LLM (Opus 4.6 with PTC) can produce and consume frames
- It is a protocol layer, not a model layer

The `CognitiveFrame` structure must survive even if the SLMs disappear.

### 4. llmfit — tool to validate the VRAM budget before committing

**Source**: [@sukhdeep7896, Twitter](https://x.com/sukhdeep7896/status/2028143775609147756)

`llmfit` analyzes hardware (RAM, CPU, GPU, VRAM) and tells you exactly which LLMs are compatible. 94 models across 30 providers, handles MoE correctly.

**To do before choosing SLMs**: run llmfit on Machine 1 (RTX 3080 10GB) to measure the actual VRAM budget with TTS + embeddings under load. Our estimate (5.5GB/10GB) is not measured — that is a risk.

---

## Recommendations

### Phase 1 — No SLMs, just the Anthropic API

- Use Anthropic API (Claude) for all routing
- Implement the PTC pattern from Phase 1 to reduce tokens (-24%)
- Structure prompts around CognitiveFrames even without SLMs
- Measure actual costs (tokens/day) before deciding if SLMs are necessary

### Phase 2 — Data-driven decision

- Run llmfit on Machine 1 to measure actual available VRAM
- If API cost exceeds X EUR/month on real volume -> evaluate SLMs
- Start with the routing SLM (most impactful) before the others
- Consider distilled models rather than custom finetunings

### What does not change
- asyncio bus architecture: independent of the LLM choice
- CognitiveFrame: survives any model change
- Machine 2 VRAM budget: still useful for the heavy generation LLM

---

## Verdict

Custom SLMs are premature optimization for Phase 1. Opus 4.6's PTC already gives -24% tokens with a single model. Start by measuring actual costs, then decide if SLMs are worth the investment. The cognitive meta-language (CognitiveFrame) remains the true architectural innovation — independent of the model choice.
