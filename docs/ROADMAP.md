# Lyra — Prioritized Roadmap

> Living document. Updated as decisions are made.
> Last updated: 2026-03-11

---

## Current focus

**Phase 1b tail — closing out the agent core**: message normalization (#139), LlmProvider protocol (#123), memory integration (#83), hub command sessions (#99), runtime config (#135), smart routing (#134). Voice pipeline (#74) is now unblocked.

---

## Phase overview

| Phase | Epic | Status | Summary |
|-------|------|--------|---------|
| **0** | #101 | ✅ Done | Bot core parity: pairing, circuit breaker, TOML templates, plugin system |
| **1** | — | ✅ Done | Hub: asyncio bus, adapters (Telegram/Discord), SimpleAgent, command router |
| **1b** | #73 | 🔄 Tail | Agent core: persona ✅, SDK agent ✅, parity audit ✅, memory foundation ✅, hub refactor ✅ |
| **Voice** | #74 | 🔓 Unblocked | TTS + STT integration — unblocked since #76 shipped |
| **2** | #60 | Planned | NATS introduction + Machine 2 coordination |
| **3** | #61 | Frozen | Atomic SLMs + cognitive pipeline |
| **4** | #62 | Frozen | Resilience, observability, security |
| **5** | #63 | Frozen | Multi-agent orchestration |

---

## Phase 1b tail (active)

> Core is done. Wrapping up CLI wrapper, memory integration, command sessions, and agent tunability.

| # | Issue | Size | Status |
|---|-------|------|--------|
| #139 | Message & Media Normalization — typed bus envelope for all adapters | L | Ready — **next** |
| #123 | LlmProvider protocol — AnthropicSdkDriver + ClaudeCliDriver + OllamaDriver | L | Analysis |
| #83 | Lyra agent integration — identity anchor, session lifecycle, L0 compaction | L | Blocked by #123 |
| #99 | Hub command sessions — /add, /explain, /summarize, /search | L | Blocked by #83 + #139 |
| #135 | Runtime agent config — `!config` live tuning without restart | S | Ready |
| #134 | LLM smart routing — complexity-based model selection | M | Ready |
| #136 | Multi-bot registry upgrade — per-bot-id routing + multi-token config | M | Blocked by #83 + #79 — do last |

**Critical path**: #139 ∥ #123 → #83 → #99

**Independent (do anytime)**: #135 (S), #134 (M), #128 (M), #80 STT (M)

**Dependencies**:
- #139 unblocks: #99, #80 (STT), #79 (TTS)
- #123 unblocks: #83
- #83 unblocks: #99, #67, #128
- #83 + #79 both needed before: #136 (multi-bot registry — not a blocker for voice)

---

## Phase 1b — Completed

| # | Issue |
|---|-------|
| #112 | Hub refactor epic (#125 scope_id, #126 per-channel queues, #127 per-session Task) |
| #75 | Agent identity / persona system |
| #76 | Direct Anthropic SDK agent (replace CLI subprocess) |
| #77 | 2ndBrain feature parity audit |
| #78 | Vault as semantic memory backend (Level 3) |
| #81 | roxabi-memory package foundation (schema v2, FTS5/BM25, namespacing) |
| #82 | Hybrid search — fastembed ONNX + sqlite-vec |
| #84 | Vault-migrate skill — v2 schema + fastembed import |
| #103 | Unified pairing system (Telegram + Discord) |
| #104 | LLM circuit breaker for Anthropic SDK calls |
| #105 | TOML message template system with i18n |
| #106 | Directory-based plugin system (MVP) |
| #111 | Bash pre-check layer before LLM monitoring calls |
| #125 | Hub: scope_id routing — replace user_id in RoutingKey |
| #126 | Hub: per-channel inbound/outbound queues + OutboundDispatcher |
| #127 | Hub: per-session Task + Discord thread auto-creation |

---

## Voice pipeline (#74)

> Partially unblocked. #80 (STT) is ready now. #79 (TTS) blocked by #136 (multi-bot registry).

| # | Issue | Priority | Status |
|---|-------|----------|--------|
| #79 | Voice TTS in Telegram (voicecli integration) | P2 | Ready |
| #80 | Voice STT — audio transcription (Whisper) | P2 | Ready |
| #42 | Automatic language detection | P2 | Ready |

---

## Memory track

| # | Issue | Priority | Status |
|---|-------|----------|--------|
| #83 | Lyra agent integration — identity anchor, L0 compaction | P1 | Open (Phase 1b tail) |
| #128 | Import 2ndBrain session history → episodic memory L3 | P2 | Open — blocked by #83 |
| #67 | Session persistence — JSONL conversation history | P2 | Open |
| #71 | Memory SLM — episodic-to-semantic promotion | P3 | Frozen |
| #72 | Memory Phase 2 — Level 4 procedural seeds | P3 | Frozen |

---

## Infrastructure

| # | Issue | Priority | Status |
|---|-------|----------|--------|
| #133 | Split Lyra adapters into independent supervisor processes | P2 | Open |
| #132 | Document Machine 2 supervisor setup + voice daemons | P2 | Open |
| #123 | Claude CLI wrapper library — extract 2ndBrain pool design | P2 | Open |

---

## Chimera strategy — Phase 2 patterns

> Patterns sourced from ClawFamily analysis. Implement after Phase 1b tail is closed.
> See `artifacts/explorations/clawfamily/chimera-strategy.md` for full design.

| # | Pattern | Source | Size | Phase |
|---|---------|--------|------|-------|
| #134 | Smart routing — complexity-based model selection | IronClaw | M | 1b tail |
| #135 | Runtime agent config — `!config` live tuning | ScalyClaw | S | 1b tail |
| — | Proactive engagement engine (2-phase cron+queue) | ScalyClaw | L | 2 |
| — | Command shield (deterministic blocklist, no LLM) | ScalyClaw | S | 2 |
| — | Memory consolidation (LLM-driven clustering) | ScalyClaw | M | 2 |
| — | Lane-based queue (cron lane separate from user) | OpenClaw | S | 2 |
| — | Diagnostic events + stuck detection | OpenClaw | M | 2 |
| — | Prompt injection scanner | OpenFang | S | 2 |

---

## Phase 2 — NATS + Machine 2 (#60)

> Start after Phase 1b tail + voice pipeline are stable.

| # | Issue | Priority |
|---|-------|----------|
| #49 | Install NATS server on Machine 1 | P2 |
| #50 | NatsBus implementation | P2 |
| #48 | Bus abstraction — LocalBus/NatsBus interface | P2 |
| #51 | LLM worker on Machine 2 — NATS-based inference service | P2 |
| #52 | Health check system — heartbeat + worker status | P2 |
| #56 | JetStream persistence — survive restarts, replay | P3 |
| #57 | NATS observability — Prometheus + Grafana | P3 |
| #58 | NATS auth — nkey/JWT | P3 |
| #23 | Machine 2 timeout + circuit breaker + cloud fallback | P2 |

---

## Business / Validation (separate track)

| # | Issue | Priority |
|---|-------|----------|
| #10 | LegalTech validation: LinkedIn posts | P1 |
| #47 | Social media strategy synthesis | P1 |
| #11 | MedTech validation: LinkedIn posts | P2 |
| #65 | Google Workspace integration (epic) | P2 |
| #14 | LLM benchmark: Qwen vs Mistral | P2 |
| #16 | LegalTech SaaS development | P3 — gated on #10 signal |
| #17 | MedTech cardio development | P3 — gated on #11 signal |
| #18 | YouTube automation pipeline | P3 |
| #19 | Meta-skills + atomic SLM | P3 |
| #20 | Polymarket agent | P3 |

---

## Do not do now

> Explicitly frozen. Reconsider when Phase 1b tail + voice are done.

- **NATS / distributed bus** — Phase 2, needs Phase 1b stable first
- **Atomic SLMs** — Phase 3, no local model benchmark done yet (#14)
- **Proactive engine** — Phase 2, no infra for it yet
- **Funding rate arbitrage** — full-time topic, incompatible with solo
- **On-chain monitoring / DeFi yield** — same
- **Multiple themed social accounts** — LinkedIn first, single account

---

## Rolling decisions

- Default LLM for Machine 2 → after benchmark #14
- LegalTech go/no-go → social signals #10 (min: 3 qualified DMs or 1 pricing request)
- MedTech go/no-go → social signals #11
- YouTube automation → after 1 validated manual workflow
- NATS adoption → after Phase 1b tail shipped and stable in production

---

## Market validation strategy

No direct interviews. Social replaces interviews: post content about niche pain points and observe signals. Engagement, DMs, comments = organic, scalable, honest validation.

**LegalTech target channels**: LinkedIn (lawyer groups), bar association forums, professional WhatsApp, legal newsletters (Dalloz Actualité, Gazette du Palais), CNB/FNUJA events.
**Content**: concrete cases drawn from Angelique — zero product pitch until signal is positive.
