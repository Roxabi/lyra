# Recap — Session 2026-03-02

> Complete summary of the strategic brainstorming session.
> Architecture, hardware, business projects, identified niches.

---

## What we did

### 1. Ecosystem analysis

Study of 4 reference projects to build Lyra:

- **OpenClaw** (Node.js) — hub-and-spoke, 50+ channels, filesystem memory, 1GB+ RAM
- **NullClaw** (Zig) — 678KB, 1MB RAM, <2ms startup, 17 channels, ultra-minimal
- **NanoBot** (Python) — 4k lines, educational, readable skeleton, clarity reference
- **OpenFang** (Rust) — 32MB, full OS agent, 40 channels, 16 security layers, knowledge graph

We also found **openclaw-python** (OpenXJarvis) — full Python port with 56+ skills, scheduler, WebSocket UI.

**Common patterns identified:**
- Universal hub-and-spoke (core isolated from channels)
- Filesystem memory for auditability
- Weight/feature spectrum (678KB to 32MB)
- OpenAI-compatible API as de facto standard

---

### 2. Lyra architecture

See `ARCHITECTURE.md` for full details.

**Summary:**
- Bus: central `asyncio.Queue`
- Routing: bindings `(channel, user_id)` to `(agent, pool_id)`
- Pools: `asyncio.Lock` per pool (sequential/user, parallel between users)
- 3-level memory: dated Markdown + BM25/embeddings + optional graph
- Security: hash-chained audit trail, prompt injection guard, least privilege
- Features: 24/7 scheduler, session persistence, auto compaction

**Language: Python + asyncio. Go/Rust/Zig/Node eliminated.**

---

### 3. Hardware & deployment

**Machine 1 — Hub (5800X + RTX 3080 10GB)**
- OS: Ubuntu Server 24.04 LTS, dual boot Windows, default Linux
- Access: SSH from Machine 2
- What runs: hub, Telegram/Discord, SQLite, TTS (~5GB VRAM), embeddings (~0.5GB)
- VRAM: ~5.5GB / 10GB — autonomous 90% of the time, never shuts down

**Machine 2 — AI Server (9800X3D + RTX 5070Ti 16GB)**
- OS: Windows
- What runs: Ollama + heavy LLM (Qwen 2.5 14B Q6 ~11GB or Gemma 3 27B Q4 ~15GB)
- CPU advantage: 9800X3D + 96MB V-Cache -> 30-40 tok/s in CPU inference (fallback)
- On demand, powered on if needed, FastAPI `/llm` API exposed on local network

**Recommended models for 16GB VRAM:**
- Qwen 2.5 14B Q6_K -> ~11GB (best balance)
- Mistral Small 24B Q4 -> ~13GB (excellent French)
- Qwen 2.5 32B Q3 -> ~13GB (more capable)
- Gemma 3 27B Q4 -> ~15GB (just fits, Google)
- DeepSeek R1 14B Q4 -> ~8GB (pure reasoning)

**Inter-machine communication:** `httpx` async HTTP/2, OpenAI-compatible API.

---

### 4. Performance risk analysis

| Area | Risk | Solution |
|------|------|----------|
| VRAM | TTS + embeddings contention | 2 machines — architecturally resolved |
| SQLite | Blocking in event loop | `aiosqlite` mandatory from the start |
| CPU embeddings | Slow without GPU | Offload to thread pool or Machine 1 GPU |
| RAM | Python overhead | Not critical at personal scale |
| CPU | Python GIL | 95% I/O-bound, asyncio is sufficient |

---

### 5. Existing Python/Go/Rust ecosystem

**Python:**
- NanoBot (HKUDS) — educational reference
- openclaw-python (OpenXJarvis) — full port, 56+ skills

**Go:**
- GoClaw (withgoclaw/go-claw) — ~10MB, 15ms startup
- ClawGo — official Go port
- Less mature ecosystem, no turnkey multi-channel hub

**Rust:**
- OpenFang — 32MB, 40+ channels, full OS agent
- ZeroClaw — 8.8MB, <5MB RAM, <10ms, Tokio
- OpenCrust — 16MB, migration path from OpenClaw
- Moltis — 60MB, built-in Web UI
- OxiCrab — panic isolation, MCP support

---

### 6. Selected business projects

**Track 1 — Automated YouTube**
Script -> voice (voicecli) -> video (MoviePy/Remotion) -> automatic publishing.
Niche: French-speaking dev/AI. Revenue: AdSense + partnerships.

**Track 2 — Micro-SaaS**
Base: roxabi_boilerplate (Bun + TurboRepo + TanStack Start + NestJS). Already ready.
Local LLM inference on Machine 2 = strong privacy argument.

**Track 3 — Social media**
Coordinated activity: @Roxabi + roxabi_site + Lyra + SaaS.
Build in public. YouTube content recycled into posts.

**Synergies:** YouTube -> clips -> posts -> SaaS traffic -> fund content.

---

### 7. Identified SaaS niches

**LegalTech (priority)**
- Market: ~70,000 lawyers in France, few digitized
- Value: 200-400 EUR/h billed -> 99-299 EUR/month no negotiation
- Domain knowledge: Angelique + compte_appart (real clients)
- Privacy argument: local LLM, data never leaves the firm
- Features: injury assessment calculation (bodily harm, divorce), document generation (conclusions, assignations — French legal filings), adverse party document analysis, case timeline, risk scoring with case law

**MedTech cardiology (on hold)**
- Direct contact: partner is a cardiologist
- Pain point #1: consultation reports = 2-3h/day of data entry
- Potential features: dictation -> structured report (voicecli already available), automatic referring physician letter, cardiovascular risk scoring (SCORE2, CHA2DS2-VASc...), pre-consultation case summary
- Constraint: avoid anything diagnostic-related (European MDR)
- Next step: interview the cardiologist about a typical workday

---

### 8. Existing projects in ~/projects

| Project | Nature | Link with Lyra |
|---------|--------|---------------|
| `roxabi_boilerplate` | Full TypeScript SaaS boilerplate | Base for all micro-SaaS |
| `roxabi_site` | Vue 3 showcase site | To animate via social media |
| `roxabi-plugins` | Claude Code plugins | Possible product extension |
| `voiceCLI` | Python TTS/STT | Already in the Lyra stack |
| `2ndBrain` | Current Telegram bot | To migrate to Lyra |
| `Angelique` | Asset calculation for separation (Python) | LegalTech proof of concept |
| `compte_appart` | Separation management (mini site) | Same |

---

### 9. Advanced memory — 5 levels

Memory architecture enriched beyond the initial 3 levels:

| Level | Name | Nature | Lifespan |
|-------|------|--------|----------|
| 0 | **Working memory** | Active context window (current messages) | Volatile |
| 1 | **Session memory** | Multi-turn session state per pool | Session duration |
| 2 | **Episodic** | Dated Markdown, immutable, human auditability | Permanent |
| 3 | **Semantic** | SQLite + BM25 + embeddings, hybrid search | Permanent |
| 4 | **Procedural** | Learned skills, memorized patterns, preferences | Permanent |

**Consolidation & time-decay:**
- Automatic compaction: summary of old turns -> semantic level
- Time-decay: relevance score that decreases (less noise in context)
- Entity extraction: people, dates, places, concepts extracted and indexed in a graph

---

### 10. Meta-skills & skill graph

**Principle:** a meta-skill orchestrates multiple atomic skills (ReAct pattern — Reason + Act).

- Atomic skills: minimal unit of action (one API, one operation)
- Meta-skill: plans, sequences, decides the next skill to call based on the result
- Skill graph: nodes = skills, edges = dependencies/compatibilities
- Planner SLM: small model (~3-7B) that selects the path through the graph

**Benefit:** dynamic composition without hardcoded logic. The hub does not know the sequences — the planner discovers them.

---

### 11. Atomic SLM — intelligent routing + cognitive meta-language

**Principle:** reserve the large LLM only for generation. Everything else -> small specialized models.

| Task | Model | Size | Target latency |
|------|-------|------|---------------|
| Routing / intent triage | SLM routing | ~1-3B | <50ms |
| Memory relevance scoring | SLM memory | ~1B | <30ms |
| Entity extraction | SLM NER | ~3B | <100ms |
| Skill selection / planner | SLM planner | ~3-7B | <200ms |
| Response generation | Full LLM | 14-27B | ~1-3s |

**Impact:** 80-90% of messages routed without touching the large model. Cost divided by 10, latency divided by 5 on simple cases.

**Cognitive meta-language (coupling between SLMs)**

SLMs do not communicate in natural language — too expensive, too ambiguous. They exchange compact cognitive structures: a meta-language internal to the hub.

Principle: each SLM produces and consumes **cognitive frames** — structured representations of the current cognitive state:

```python
@dataclass
class CognitiveFrame:
    intent: str          # "search_memory" | "call_skill" | "generate" | ...
    entities: list[str]  # extracted entities (people, places, concepts)
    context_refs: list[str]  # IDs of relevant memories (level 2/3)
    skill_path: list[str]    # planned skill sequence
    confidence: float        # planner confidence score
    emotional_tone: str | None  # "neutral" | "urgent" | "factual" ...
    metadata: dict
```

**Full cognitive flow:**
```
raw message
    -> SLM routing     -> intent + entities
    -> SLM memory      -> context_refs (relevant memories)
    -> SLM planner     -> skill_path (graph traversal)
    -> executed skills  -> structured results in the frame
    -> full LLM        -> final generation (only if needed)
    -> SLM NER         -> entity extraction for memory update
```

**Meta-language advantages:**
- Each SLM receives minimal, structured input (not the full context)
- Frames are logged -> complete auditability of reasoning
- Reusable: a partial frame can be picked up by another agent
- Extensible: new fields can be added without changing the architecture

---

### 12. Crypto — identified ideas

**Priority 1 — Funding rate arbitrage**
- Long position on spot + short on perpetual (or vice versa)
- Captures funding every 8h without directional exposure
- Implementation: agent monitors rates on Binance/Bybit/OKX, triggers when rate > threshold
- Risk: liquidation if spread widens sharply, funding fees

**Polymarket + local LLM**
- Prediction market: bets on real events
- LLM analyzes probabilities, compares with market price, identifies mispricings
- Edge: analysis speed + knowledge base -> exploit recent poorly-priced events

**On-chain monitoring**
- Whale wallet surveillance, CEX/DEX flows, large movements
- Agent alerts in real time on patterns (accumulation, distribution)
- Stack: Etherscan/Alchemy API + Telegram alert

**Yield DeFi (passive)**
- Liquidity farming on stable pools (USDC/USDT) -> 5-15% APY without directional risk
- Automatic rebalancing between protocols (Aave, Curve, Morpho)
- Risk: smart contract, depeg, impermanent loss (minimal on stables)

**CEX/DEX arbitrage**
- Price differences between centralized and decentralized exchanges
- Latency critical — advantage if Machine 2 is co-located or near a node
- More complex, requires capital and dedicated infrastructure

**To avoid:** MEV (front-running) — domain of validators and specialized teams, very high barrier to entry.

---

## Key session decisions

- Python + asyncio for the entire hub (Go eliminated)
- Ubuntu Server 24.04 LTS on Machine 1 (dual boot, default Linux)
- Machine 1 autonomous, Machine 2 on demand via local API
- `aiosqlite` mandatory from the start
- Cloud LLM (Anthropic) by default, local LLM = fallback/offline/cost
- Never launch voicecli manually from Claude Code when the bot is running
- LegalTech as priority SaaS, MedTech cardiology on hold
- **Market validation = social media**, not direct interviews — post about niche pain points, observe signals (engagement, DMs, comments)

---

## Prioritized roadmap

### P0 — Blocking foundations (nothing can start without this)

| Action | Deliverable |
|--------|------------|
| Machine 1 -> Ubuntu Server 24.04 LTS (dual boot) | Base infrastructure operational, SSH from Machine 2 |
| Lyra hub prototype: bus + bindings + pools | Functional asyncio core, a few hundred lines |

### P1 — Long-lead: start now (results in 3-6 months)

These actions have a **compounding latency** — each week of delay is permanently lost.

| Action | Time to results | Risk of not starting early |
|--------|----------------|---------------------------|
| YouTube: create the channel + publish first 3 videos | 3-6 months | The algorithm indexes slowly. No audience = no AdSense revenue or partnerships |
| Social media: thematic accounts for LegalTech + MedTech + dev/AI | 2-4 months | Audience = market validation + long-term asset |
| LegalTech validation via social (10-20 posts, engagement/DM signals) | 1-2 months | The market responds directly. Scalable vs direct interviews |
| MedTech validation via social (10-20 posts, engagement/DM signals) | 1-2 months | Same — content = market probe |

### P2 — Quick validation (short feedback, a few days/weeks)

| Action | Test to run | Success criteria |
|--------|------------|-----------------|
| Machine 2 + Ollama + Qwen 2.5 14B | Benchmark tok/s, French quality, internal API | >20 tok/s, correct French responses |
| Funding rate arbitrage | **Paper trading 2-3 weeks** (Binance testnet) before real capital | Net annualized rate after fees > 8% |
| Telegram migration -> Lyra hub | Plug in existing adapter, real traffic | Bot responds without regression |

### P3 — Gated (requires prerequisites)

| Action | Unblocked by |
|--------|-------------|
| LegalTech SaaS (development) | Positive social signals + functional Lyra hub |
| MedTech cardiology | Positive social signals on medical niche |
| YouTube automation pipeline | Lyra hub + 1 validated manual workflow first |
| Meta-skills + atomic SLM | Functional hub + existing skill base |
| Polymarket agent | Machine 2 operational + validated local LLM |

### Mandatory POCs before committing

- **Hub**: test the asyncio queue with 2-3 mocked adapters — validates architecture before migration
- **Local LLM**: comparative benchmark Qwen 2.5 14B vs Mistral Small 24B on real cases (French, code, reasoning) before choosing
- **Funding rate**: paper trading 2-3 weeks minimum, measure net after fees and spread
- **LegalTech**: 10-20 social posts + positive signals (engagement, DMs) before the first line of code

---

## Generated voice memos

- `TTS/texts_in/nouveau_moteur_archi.md` — overall architecture + features (1185 words, ~7 min)
- `TTS/texts_in/nouveau_moteur_detail.md` — feature details + language choice (2091 words, ~14 min)
- `TTS/texts_in/nouveau_moteur_hardware.md` — performance constraints + 2-machine architecture (913 words, ~6 min)
- `TTS/texts_in/lyra_machine2_projets.md` — LLM models + business ideas (802 words, ~5 min)
- `TTS/texts_in/lyra_vision_globale.md` — overall vision by Lyra (5 min)
