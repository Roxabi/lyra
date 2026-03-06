# Lyra — Prioritized Roadmap

> Living document. Updated as decisions are made.
> Last updated: 2026-03-06

---

## Current focus

**Phase 1b — Agent core**: give Lyra an identity, replace CLI subprocess with direct SDK, audit 2ndBrain parity, connect vault as memory backend.

Epic: #73

---

## Phase overview

| Phase | Epic | Status | Summary |
|-------|------|--------|---------|
| **1 (P0)** | — | **Done** | Hub: asyncio bus, adapters (Telegram/Discord), SimpleAgent, command router |
| **1b** | #73 | **Active** | Agent core: persona, Anthropic SDK, skills audit, vault memory |
| **2** | #60 | Planned (P2) | NATS introduction + Machine 2 coordination |
| **3** | #61 | Planned (P3) | Atomic SLMs + cognitive pipeline |
| **4** | #62 | Planned (P3) | Resilience, observability, security |
| **5** | #63 | Planned (P3) | Multi-agent orchestration |
| **Voice** | #74 | Planned (P2) | TTS + STT integration (Telegram first) |

---

## Phase 1b — Agent core (active)

> Prerequisite: Phase 1 hub stable (done).

| # | Issue | Priority | Status |
|---|-------|----------|--------|
| #75 | Agent identity / persona system | P1 | Open |
| #76 | Direct Anthropic SDK agent (replace CLI subprocess) | P1 | Open |
| #77 | 2ndBrain feature parity audit | P1 | Open |
| #78 | Vault as semantic memory backend (Level 3) | P1 | Open |

**Recommended order**: #75 (persona) → #76 (SDK) → #77 (audit) → #78 (vault)

**Dependencies**:
- #76 unblocks: vault memory (#78), voice pipeline (#74), skills migration
- #78 relates to: Memory epic #9, #71 (episodic→semantic), #72 (procedural seeds)

---

## Memory track

| # | Issue | Priority | Status |
|---|-------|----------|--------|
| #9 | Memory layer Phase 1: levels 0 + 3 (epic) | P1 | Open |
| #67 | Session persistence — JSONL conversation history | P2 | Open |
| #71 | Memory SLM — episodic-to-semantic promotion | P3 | Open |
| #72 | Memory Phase 2 — Level 4 procedural seeds | P3 | Open |

---

## Voice pipeline (#74)

> Prerequisite: Phase 1b (direct SDK agent).

| # | Issue | Priority | Status |
|---|-------|----------|--------|
| #79 | Voice TTS in Telegram (voicecli) | P2 | Open |
| #80 | Voice STT — audio transcription (Whisper) | P2 | Open |

---

## Other open issues

### Engine (P2)

| # | Issue |
|---|-------|
| #42 | Automatic language detection (voice/text) |
| #44 | Event-driven agent monitoring |
| #23 | Machine 2 timeout + circuit breaker + cloud fallback |

### Business / Validation (separate track)

| # | Issue | Priority |
|---|-------|----------|
| #10 | LegalTech validation: LinkedIn posts | P1 |
| #47 | Social media strategy synthesis | P1 |
| #11 | MedTech validation: LinkedIn posts | P2 |
| #65 | Google Workspace integration (epic) | P2 |
| #12 | YouTube: channel + first videos | P3 |
| #13 | Machine 2 + Ollama + Qwen setup | P3 |
| #14 | LLM benchmark: Qwen vs Mistral | P2 |
| #16 | LegalTech SaaS development | P3 |
| #17 | MedTech cardio development | P3 |
| #18 | YouTube automation pipeline | P3 |
| #19 | Meta-skills + atomic SLM | P3 |
| #20 | Polymarket agent | P3 |

---

## Completed (Phase 1)

| # | Issue |
|---|-------|
| #5 | CLAUDE.md + Python scaffold |
| #6 | Machine 1 → Ubuntu Server 24.04 |
| #7 | Hub prototype: asyncio bus + bindings + pools |
| #8 | Hub POC: mocked adapters + validation |
| #15 | Telegram + Discord adapters connected |
| #21 | Architecture gaps resolved |
| #22 | Non-blocking embedding strategy evaluated |
| #25 | Restricted AI agent account |
| #26, #28–31 | Hub skeleton slices |
| #35 | SimpleLyraAgent — Claude CLI subprocess |
| #45 | Two-level memory design |
| #64 | External tool integration pattern (ADR-010) |
| #66 | Command/skill router |
| #68 | Data migration: 2ndBrain → roxabi-vault |

---

## Do not do now

> Explicitly frozen. Reconsider when Phase 1b is complete.

- **NATS / distributed bus** — Phase 2, individual issues collapsed into epic #60
- **Atomic SLMs** — Phase 3, collapsed into epic #61
- **Funding rate arbitrage** — full-time topic, incompatible with solo launch
- **On-chain monitoring / DeFi yield** — same
- **Multiple themed social accounts** — LinkedIn first, single account

---

## Rolling decisions

- Default LLM for Machine 2 → after benchmark #14
- LegalTech go/no-go → social signals #10
- MedTech go/no-go → social signals #11
- YouTube automation → after 1 validated manual workflow

---

## Market validation strategy

No direct interviews. Social replaces interviews: post content about niche pain points and observe signals. Engagement, DMs, comments = organic, scalable, honest validation.

**LegalTech target channels**: LinkedIn (lawyer groups), Twitter/X, specialized forums.
**Content**: concrete pain points — zero product pitch until the signal is positive.
