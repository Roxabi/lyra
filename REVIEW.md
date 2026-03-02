# Lyra — Document Review
> Generated on 2026-03-02 by two specialized agents (architect + product-lead)
> Source: ARCHITECTURE.md, RECAP.md, ROADMAP.md

---

## Architecture — Review

### Strengths
- Hub-and-spoke asyncio bus validated in production on much larger projects
- Rigorous P1/P2 phasing — SLMs properly gated in Phase 2
- SQLite + aiosqlite = good choice, well reasoned
- Canonical `user_id` decoupled from platform IDs — good abstraction for cross-channel memory
- Cloud-first LLM eliminates Machine 2 as a blocking dependency in Phase 1
- Explicit separation of architectural decisions / technical constraints

### Blocking gaps (to resolve before coding)

**1. Return path not specified**
The diagram shows how a message enters the hub, not how the response goes back to the channel. Does the hub maintain an adapter registry? Absent from the spec — fundamental gap.

**2. Pool/agent relationship unclear**
Is each agent a singleton shared across pools, or one instance per pool? A stateful singleton creates a race condition risk. This choice is not documented.

**3. Phase 1 memory scope too broad**
5 levels planned in Phase 1. Levels 0 (working) and 3 (semantic) are sufficient to start. Levels 1 (session), 2 (episodic), 4 (procedural) → add when a real need arises.

### Technical risks

- **VRAM not measured**: 5.5GB/10GB is an estimate, not a measurement under real load (TTS + embeddings simultaneously). Measure with `nvidia-smi` before planning Phase 2 SLMs.
- **Machine 2 under Windows**: no automatic startup daemon. Who restarts the LLM server after reboot? Not specified.
- **Backpressure**: unbounded `asyncio.Queue`. If the LLM is slow, messages accumulate silently. Behavior under burst not defined.
- **Synchronous embeddings**: `sentence-transformers` blocks the event loop without `run_in_executor`. 2-5s latencies on requests with semantic search. Solution not decided.
- **Machine 2 fallback not defined**: if Machine 2 is powered off, no timeout or circuit breaker specified → pool blocked indefinitely.

### Decisions to challenge

- **Internal FastAPI on Machine 1**: for communication between components in the same process, a plain Python function call suffices. Justification absent.
- **Hash-chained audit trail**: useful feature for compliance/legal, but unnecessary for personal use. P3 feature disguised as Phase 1.
- **`sentence-transformers`**: heavy (PyTorch, ~1GB). Plan to migrate to `fastembed` or embeddings via Ollama (available in P2).
- **Procedural memory**: the implementation boundary with semantic memory is blurry. Prefer a `type='procedure'` tag in the semantic table rather than a separate table.

### Priority actions — Architecture

1. Specify response routing in the diagram (return path)
2. Document the pool/agent choice: singleton or instance?
3. Reduce Phase 1 memory scope to levels 0 and 3 only
4. Add automatic SQLite backup (daily cron, 30 minutes of work)
5. Define the backpressure strategy (communicating an ETA to the user is recommended)
6. Add `APScheduler` to the stack for the embedded scheduler
7. Define timeout + cloud fallback when Machine 2 does not respond
8. Measure actual Machine 1 VRAM under load before planning SLMs

---

## Roadmap & Business — Review

### Strengths
- Not coding the SaaS before a market signal = mature decision, rare among solos
- Explicit freeze of topics (funding rate, Phase 1 SLMs, multiple social accounts) well documented with justifications
- Cloud-first LLM: no Machine 2 dependency to get started
- **Under-leveraged asset: Angelique exists.** Real client, validated domain knowledge on patrimony calculations. LinkedIn posts should start from concrete cases drawn from Angelique — not generic pain points. A real, quantified situation is 10x more effective than a generic "lawyers waste time" post.
- Honesty about crypto: freezing funding rate by saying "full-time topic, real capital" is proof of lucidity.
- The 6 challenge docs demonstrate a real capacity for self-criticism.

### Strategic risks

**1. LinkedIn ≠ French lawyers (false negative risk)**
French bar associations do not massively consume professional content on LinkedIn. They are on bar association forums, professional WhatsApp groups, CNB/FNUJA events, Dalloz Actualite newsletters, Gazette du Palais. 20 posts without a DM could be a channel false negative — not a niche rejection. These are not the same thing, and confusing the two would block P3 unjustly.

**2. Go/no-go criteria too vague**
"Positive signals" is not measurable. Proposed thresholds:
- 3 qualified DMs (concrete pain point described, not generic curiosity)
- At least 1 demo or pricing request
- Or 1 in-depth conversation with a practicing lawyer

**3. Week 1 unrealistic for a solo**
Physical dual boot + asyncio hub + Telegram connected + 10 LinkedIn posts = 4 cognitive modes in 7 days. If the dual boot takes D1-D3 (very possible), everything else slips.

**4. Capacity planning absent**
The roadmap lists items, not available hours. Without a real time budget (X hours/week, Y% infra / Z% code / W% content), it is wishful thinking.

**5. LegalTech scale maintenance not budgeted**
ONIAM scales, Cour de cassation case law, Gazette du Palais scales change regularly. This is ongoing editorial work, not just code. Not budgeted anywhere.

**6. LegalTech competition underestimated**
Doctrine.ai is already established in France. API access to official case law databases (Legifrance, Judilibre) has conditions not mentioned. The "local LLM + confidentiality" argument is solid but not differentiating long-term — any competitor can deploy a local model.

The real differentiating value: automated damage calculations with the correct, up-to-date scales (IPP, AIPP, Gazette du Palais). This is precise and hard to replicate without domain knowledge.

### Priority actions — Roadmap

1. Define a quantified threshold for the LegalTech go/no-go (minimum 3 qualified DMs)
2. Identify alternative channels to LinkedIn for reaching French lawyers (bar association forums, professional WhatsApp, legal newsletters, CNB events)
3. Rewrite LinkedIn posts starting from concrete cases drawn from Angelique
4. Break down Week 1 into actual capacity (available hours/day)
5. Add a capacity budget to the roadmap (total hours/week + allocation)
6. Plan editorial maintenance of scales if LegalTech moves to P3

---

## Executive summary

The architecture is on the right track — the fundamental decisions are defensible and well sourced. Three spec gaps to fill before coding (response routing, pool/agent, memory scope).

The roadmap has a sound philosophy but two critical blind spots: the go/no-go criteria are not measurable, and LinkedIn may not reach the right audience to validate the LegalTech market.

The project's strongest asset is Angelique — a real prototype with a real client. It is under-leveraged in the validation strategy.
