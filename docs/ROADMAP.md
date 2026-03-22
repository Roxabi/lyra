# Lyra — Prioritized Roadmap

> Living document. Updated as decisions are made.
> Last updated: 2026-03-22

---

## Current focus

**Phase 1b complete. Architecture refactoring complete.** All Phase 1b items shipped + full module decomposition + hardening. Next: Phase 2 (#60) or #136 (multi-bot registry, blocked by #79).

---

## Phase overview

| Phase | Epic | Status | Summary |
|-------|------|--------|---------|
| **0** | #101 | ✅ Done | Bot core parity: pairing, circuit breaker, TOML templates, plugin system |
| **1** | — | ✅ Done | Hub: asyncio bus, adapters (Telegram/Discord), SimpleAgent, command router |
| **1b** | #73 | ✅ Done | Agent core: persona ✅, SDK agent ✅, parity audit ✅, memory foundation ✅, hub refactor ✅, command sessions ✅, architecture refactoring ✅ |
| **Voice** | #74 | 🔓 Unblocked | TTS + STT integration — unblocked since #76 shipped |
| **2** | #60 | Planned | NATS introduction + Machine 2 coordination |
| **3** | #61 | Frozen | Atomic SLMs + cognitive pipeline |
| **4** | #62 | Frozen | Resilience, observability, security |
| **5** | #63 | Frozen | Multi-agent orchestration |

---

## Phase 1b tail (complete)

> All core features shipped. Architecture refactoring complete.

| # | Issue | Size | Status |
|---|-------|------|--------|
| #139 | Message & Media Normalization — typed bus envelope for all adapters | L | ✅ Done |
| #123 | LlmProvider protocol — AnthropicSdkDriver + ClaudeCliDriver | L | ✅ Done |
| #134 | LLM smart routing — complexity-based model selection | M | ✅ Done |
| #135 | Runtime agent config — `!config` live tuning without restart | S | ✅ Done |
| #151 | AuthMiddleware + TrustLevel per adapter | M | ✅ Done |
| #152 | RoutingContext + outbound verification | M | ✅ Done |
| #83 | Lyra agent integration — identity anchor, session lifecycle, L0 compaction | L | ✅ Done |
| #99 | Hub command sessions — /vault-add, /explain, /summarize, /search | L | ✅ Done |
| #136 | Multi-bot registry upgrade — per-bot-id routing + multi-token config | M | Blocked by #83 + #79 — do last |

**Remaining**: #136 (blocked by #79)

---

## Architecture refactoring (complete)

> Module decomposition + hardening — shipped 2026-03-16/17.

| # | Issue | Size | Status |
|---|-------|------|--------|
| #294 | Decompose hub.py — extract MessagePipeline, AudioPipeline, PoolManager | L | ✅ Done |
| #295 | Decompose agent.py into focused modules | L | ✅ Done |
| #296 | Decompose discord.py — extract formatting, audio, threads | L | ✅ Done |
| #297 | Decompose telegram.py, multibot.py, cli_agent.py, memory.py | L | ✅ Done |
| #298 | Decompose command_router.py — extract builtin + workspace commands | M | ✅ Done |
| #300 | Extract PoolProcessor from pool.py | M | ✅ Done |
| #304 | Decompose agent_store.py — extract TOML seeder | M | ✅ Done |
| #313 | Split AuthMiddleware into Authenticator + GuardChain | M | ✅ Done |
| — | Deduplicate 8 patterns across codebase | M | ✅ Done |
| — | Simplify architecture — remove dead abstractions and duplication | M | ✅ Done |
| #317 | Harden timeout system and reaper process | M | ✅ Done |
| #318 | Wire session_id + reply_message_id for resumption | S | ✅ Done |

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
| #123 | LlmProvider protocol — ClaudeCliDriver + AnthropicSdkDriver |
| #134 | LLM smart routing — complexity-based model selection |
| #135 | Runtime agent config — `!config` live tuning |
| #139 | Message & Media Normalization — typed bus envelope |
| #140 | InboundAudio envelope + normalize_audio() |
| #141 | OutboundAudio envelope + render_audio() |
| #143 | Outgoing attachment handling via OutboundMessage |
| #144 | OutboundAudioChunk + render_audio_stream |
| #151 | AuthMiddleware + TrustLevel per adapter |
| #152 | RoutingContext + outbound verification |
| #172 | InboundAudioBus — per-platform bounded queues |
| #173 | Wire InboundAudio enqueue in Telegram + Discord |
| #174 | Audio consumer loop for InboundAudio → STT routing |
| #175 | OutboundDispatcher.enqueue_audio() with CB ownership |
| #183 | Extract inbound attachments |
| #184 | OutboundAttachment + render_attachment() |
| #196 | Enforce Python complexity and size limits |
| #203 | Replace blocking os.write/os.close in _audio_loop with async I/O |
| #204 | Extract PoolContext protocol to decouple Pool → Hub |
| #205 | TTL eviction for Hub.pools |
| #207 | Two-tier /health endpoint |
| #211 | pytest-cov and coverage gate |
| #212 | hmac.compare_digest for webhook secrets |
| #215 | Resolve symlink in plugin_loader before exec_module |
| #217 | OutboundDispatcher.enqueue_attachment() with CB ownership |
| #220 | Extract ProviderError to replace AnthropicAPIError in core |
| #83 | Memory agent integration — Pool identity, MemoryManager, identity anchor, session flush, compaction, cross-session recall, concept/preference extraction |

---

## Voice pipeline (#74)

> STT shipped (#80 ✅). TTS unblocked — #79 (Telegram) + #232 (Discord) depend on #167 (TTSService wrapper).
> Note: #136 (multi-bot registry) depends on #79, not the other way around.

| # | Issue | Priority | Status |
|---|-------|----------|--------|
| #167 | Formalize voiceCLI consumption — TTSService wrapper | P2 | Ready — **next** |
| #79 | Voice TTS in Telegram (voicecli integration) | P2 | Ready — blocked by #167 |
| #232 | Voice TTS in Discord (voicecli integration) | P2 | Ready — blocked by #167 + #79 |
| #80 | Voice STT — audio transcription (Whisper) | P2 | ✅ Done |
| #42 | Automatic language detection | P2 | Ready |

---

## Memory track

| # | Issue | Priority | Status |
|---|-------|----------|--------|
| #83 | Lyra agent integration — identity anchor, L0 compaction | P1 | ✅ Done |
| #128 | Import 2ndBrain session history → episodic memory L3 | P2 | Open — blocked by #83 |
| #67 | Raw turn logging — TurnStore (L1 memory layer) | P2 | ✅ Done |
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
| #134 | Smart routing — complexity-based model selection | IronClaw | M | ✅ Done |
| #135 | Runtime agent config — `!config` live tuning | ScalyClaw | S | ✅ Done |
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

## Paradigm enforcement

> Python-first, CLI/Library paradigm adopted 2026-03-08. Formalized in #165.

When onboarding a new project or reviewing an existing one, verify:

- [ ] Package has `__init__.py` with `__all__` (library contract)
- [ ] `cli.py` contains zero business logic (thin shell only)
- [ ] Models/engines load lazily (no side effects on import)
- [ ] Cross-project dependencies declared as `uv add --editable path/to/lib`
- [ ] No HTTP server used as an inter-project integration layer

Projects needing a library API review (tracked separately): `roxabi_boilerplate` (NestJS backend → FastAPI migration?), `ryvo` (same), `roxabi_site` (Vue/TS frontend — frontend stays TS).

See `docs/ARCHITECTURE.md` → **Python-first Paradigm** for the full definition.

---

## Refactoring policy

Feature work accumulates silently. `core/` had grown to 60+ files; over a dozen exceeded 300 lines at peak. Without a cadence, the mental model degrades and agent context costs rise.

**Cadence:** for every 2 feature issues closed, open 1 refactor issue.

**Scope of refactor issues:**
- File size — split files exceeding 300 lines
- Directory size — break up directories with 20+ files
- Duplication — consolidate repeated patterns
- Unused code — remove dead modules, functions, and imports

**Goal:** minimize total file count. Fewer files = faster agent mental models = lower error rate.

**Triage steps (on feature issue close):**
1. Have 2 feature issues been closed since the last refactor issue was opened? → open a refactor issue if yes
2. Did this feature add new files that are already oversized? → open a targeted refactor issue immediately

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
