# Lyra — Prioritized Roadmap

> Living document. Updated as decisions are made.
> Last updated: 2026-03-02

---

## Current scope lock

**Only one axis to validate in the next 30 days: LegalTech via social.**

Everything else is frozen — not deleted, not forgotten. Frozen until a clear signal emerges.

---

## Week 1 — Day-by-day tasks

| Day | Action | Deliverable |
|-----|--------|-------------|
| D1–D2 | Machine 1 → Ubuntu Server 24.04 LTS (dual boot, Linux as default) | SSH from Machine 2 operational |
| D3–D4 | Hub skeleton: `asyncio.Queue` + bindings dict + `get_or_create_pool()` | ~150 lines, one test message traversing the bus |
| D5 | Connect the existing Telegram adapter | One real Telegram message routed through the new hub |
| D6–D7 | First 10 LegalTech LinkedIn posts | Content about lawyer/notary pain points — zero product pitch |

---

## P0 — Blocking foundations

> Nothing can start without this. Strict sequence.

| # | Action | Deliverable | Dependencies |
|---|--------|-------------|--------------|
| 1 | Machine 1 → Ubuntu Server 24.04 LTS | Operational infra, SSH | — |
| 2 | Hub prototype: bus + bindings + pools | `asyncio.Queue` + routing + `Lock` per pool | Machine 1 up |
| 3 | POC hub: 2-3 mocked adapters | Messages routed without deadlock | Hub prototype |

---

## P1 — Long-lead: start now

> Compounding latency — every week of delay is permanently lost.

| # | Action | Time to results | Risk if delayed |
|---|--------|-----------------|-----------------|
| 4 | LegalTech validation: 10-20 posts about lawyer/notary pain points, observe signals | 1-2 months | Direct market signal — scalable, honest, produces value even without conversion |
| 5 | MedTech validation: 10-20 posts about medical dictation + reports | 1-2 months | Same strategy, more constrained niche (MDR) |
| 6 | YouTube: create channel + publish first 3 manual videos | 3-6 months | The algorithm indexes slowly — no audience = no AdSense or partnerships |

> **Note**: Themed social media accounts are created only after LinkedIn posts have generated positive signals. Not before.

---

## P2 — Quick validations

> Short feedback loop. A few days to a few weeks.

| # | Action | Test to run | Success criteria |
|---|--------|-------------|------------------|
| 7 | Machine 2 + Ollama + Qwen 2.5 14B | Benchmark tok/s, French quality, internal API | >20 tok/s, correct answers in FR |
| 8 | LLM benchmark: Qwen 2.5 14B vs Mistral Small 24B | FR writing, code, reasoning | Choose the default model |
| 9 | Telegram migration → Lyra hub | Connect existing adapter, real traffic | Bot responds without regression |

---

## P3 — Gated

> Requires strict prerequisites. Do not start before they are met.

| # | Action | Unblocked by |
|---|--------|--------------|
| 10 | LegalTech SaaS — development | Positive social signals #4 + Hub #2 functional |
| 11 | MedTech cardio — development | Positive social signals #5 |
| 12 | YouTube automation pipeline | Functional hub + 1 validated manual workflow (#6) |
| 13 | Meta-skills + atomic SLM + cognitive meta-language | Hub Phase 1 stable + Machine 1 VRAM budget measured |
| 14 | Polymarket agent | Machine 2 operational (#7) + Local LLM validated (#8) |

---

## Mandatory POCs

> Must be done **before** committing to each axis.

| POC | Objective | Go/no-go criteria |
|-----|-----------|-------------------|
| Mocked asyncio hub | Validate bus + bindings architecture before migration | Messages routed correctly, no deadlock |
| LLM benchmark Machine 2 | Choose the default model | Acceptable quality + tok/s |
| Social LegalTech (10-20 posts) | Validate pain point + market interest | Engagement, inbound DMs, product questions |
| Social MedTech (10-20 posts) | Same for the medical niche | Same |

---

## Timeline view

```
Week 1
├── [P0] Machine 1 → Ubuntu Server (D1–D2)
├── [P0] Hub skeleton ~150 lines (D3–D4)
├── [P0] Telegram connected to the hub (D5)
└── [P1] First 10 LegalTech LinkedIn posts (D6–D7)

Weeks 2–4
├── [P0] POC hub validated (3 mocked adapters)
├── [P1] Continue LegalTech posts + observe signals
└── [P1] First MedTech posts

~Months 1–2
├── [P2] Machine 2 + Ollama + LLM benchmark
├── [P2] Telegram migration → Lyra hub (real traffic)
└── [P1] YouTube channel + first 3 manual videos

~Months 2–3
└── [P3] LegalTech SaaS (if positive signals)

~Months 3–6
├── [P3] YouTube automation pipeline
└── [P3] Meta-skills + atomic SLM (if VRAM available)
```

---

## Rolling decisions

- Default LLM model for Machine 2 → after benchmark #8
- LegalTech: go/no-go for development → social signals #4 (engagement, DMs, product questions)
- MedTech: go/no-go → social signals #5
- YouTube automation: go → after 1 validated manual workflow

---

## Do not do now

> Explicitly frozen. Reconsider when the hub is running and a SaaS is converting.

- **Funding rate arbitrage** — full-time topic, real capital, dedicated infra. Not compatible with a solo launch.
- **Polymarket + LLM** — depends on validated Machine 2, so earliest at P3.
- **On-chain monitoring / DeFi yield** — same priority issue.
- **Atomic SLM Phase 1** — Machine 1 VRAM budget not measured. Moved to Phase 2 (see ARCHITECTURE.md).
- **Multiple themed social accounts** — creating one account per niche without prior signal = scattered effort. LinkedIn first, single account.

---

## Market validation strategy

No direct interviews. Social replaces interviews: post content about niche pain points (lawyers, doctors) and observe signals. Engagement, DMs, comments = organic, scalable, honest validation. The content produces value even if the niche does not convert.

**LegalTech target channels**: LinkedIn (lawyer groups, bar associations), Twitter/X (active legal professionals), specialized forums.
**Content**: concrete pain points (wasted time, repetitive data entry, manual calculations) — zero product pitch until the signal is positive.
