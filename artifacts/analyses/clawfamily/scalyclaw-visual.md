# ScalyClaw — Visual Architecture Explainer

> Companion to `scalyclaw.md`
> Date: 2026-03-10

---

## 1. Big Picture: What ScalyClaw Is

```
╔══════════════════════════════════════════════════════════════════╗
║                        SCALYCLAW                                 ║
║              "sudo for AI — one mind, all channels"              ║
║                                                                  ║
║  ┌──────┐ ┌──────────┐ ┌──────┐ ┌─────────┐ ┌──────┐ ┌──────┐ ║
║  │  TG  │ │ Discord  │ │Slack │ │WhatsApp │ │Signal│ │Teams │ ║
║  └──┬───┘ └────┬─────┘ └──┬───┘ └────┬────┘ └──┬───┘ └──┬───┘ ║
║     └──────────┴───────────┴──────────┴──────────┴────────┘     ║
║                            │ normalize                           ║
║                     ┌──────▼──────┐                             ║
║                     │  NODE (1x)  │ ◄── one brain               ║
║                     └──────┬──────┘                             ║
║                            │ BullMQ jobs                         ║
║         ┌──────────────────┼──────────────────┐                 ║
║  ┌──────▼──────┐  ┌────────▼────────┐  ┌──────▼──────┐         ║
║  │  WORKER 1   │  │    WORKER 2     │  │  WORKER N   │         ║
║  │  (local)    │  │   (local)       │  │  (remote!)  │         ║
║  └─────────────┘  └─────────────────┘  └─────────────┘         ║
║                                                                  ║
║              only shared resource: REDIS                         ║
╚══════════════════════════════════════════════════════════════════╝
```

**Key insight**: Workers can run on any machine. They only need Redis. This is the architectural unlock that makes ScalyClaw production-grade.

---

## 2. Message Journey (Happy Path)

```
 User sends "Generate a PDF report of AAPL stock"

 [Telegram]
     │  raw Telegram event
     ▼
 [Channel Adapter]
     │  normalize → InboundMessage { sender, text, channel, session }
     ▼
 ┌─────────────────────────────────────────┐
 │            GUARD PIPELINE               │
 │                                         │
 │  1. Echo Guard      → PASS              │
 │     (no injection detected)             │
 │                                         │
 │  2. Content Guard   → PASS              │
 │     (no harmful content)                │
 │                                         │
 │  3. Command Shield  → PASS              │
 │     (no dangerous shell patterns)       │
 │                                         │
 │  All 4 must pass — one fail = dropped   │
 └───────────────────┬─────────────────────┘
                     │
                     ▼
           [ORCHESTRATOR / LLM Loop]
                     │
                     │  LLM decides: use stock-price-skill + html-to-pdf-skill
                     ▼
         ┌───────────────────────┐
         │  BullMQ: skill queue  │
         │  job { skill: "stock-price", params: { ticker: "AAPL" } }
         │  job { skill: "html-to-pdf", params: { data: ... } }
         └───────────┬───────────┘
                     │  Redis
                     ▼
             [WORKER processes jobs]
                     │  runs main.py / main.js in subprocess
                     │  deps auto-installed if missing
                     │
                     ▼
             [Result back via Redis]
                     │
                     ▼
           [ORCHESTRATOR formats response]
                     │
                     ▼
          [Memory Extractor]
              extracts: user wants stock reports, prefers AAPL, PDF format
                     │
                     ▼
          [Telegram: sends PDF]
```

---

## 3. Three Processes At a Glance

```
┌─────────────────────────────────────────────────────────────────┐
│  PROCESS 1: NODE (singleton)                                     │
│                                                                  │
│  channels/       ← 7 adapters (Telegraf, discord.js, etc.)      │
│  guards/         ← Echo, Content, Skill/Agent, Command Shield    │
│  orchestrator/   ← LLM loop + tool decision                      │
│  memory/         ← SQLite + sqlite-vec + FTS5                    │
│  agents/         ← dispatch to BullMQ agents queue               │
│  skills/         ← dispatch to BullMQ skill queue                │
│  scheduler/      ← cron + proactive messages                     │
│  mcp/            ← MCP registry (stdio/HTTP/SSE)                 │
│  models/         ← provider registry + priority+weight routing   │
│  api/            ← Fastify HTTP for dashboard                    │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  PROCESS 2: WORKER (1-N, horizontal)                            │
│                                                                  │
│  BullMQ consumer (skill queue + agents queue)                   │
│  Code execution:                                                 │
│    JavaScript → bun run                                          │
│    Python     → uv run                                           │
│    Rust       → cargo run --release                              │
│    Bash       → bash                                             │
│  Skill cache + dependency manager                               │
│  Reads vault secrets from Redis → injects as env vars           │
│  Workers on remote machine? Just point to same Redis.           │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  PROCESS 3: DASHBOARD (React SPA)                               │
│                                                                  │
│  15 views: Overview · Mind · Usage · Channels · Models           │
│           Agents · Skills · Memory · Vault · MCP                │
│           Scheduler · Engagement · Security · Logs · Workers    │
│                                                                  │
│  Personality editor (IDENTITY.md / SOUL.md / USER.md)           │
│  Zero-downtime config changes via Redis pub/sub                 │
│  Chat overlay (test without leaving the dashboard)              │
│  Job inspector (see BullMQ queues live)                         │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. Security: 4 Guard Layers

```
   INCOMING MESSAGE
        │
        ▼
┌───────────────────┐
│   ECHO GUARD      │ Pattern matching
│                   │ "Is the AI being tricked into
│                   │  repeating injected text?"
│   PASS ──►        │
└───────────────────┘
        │
        ▼
┌───────────────────┐
│  CONTENT GUARD    │ LLM judge
│                   │ "Prompt injection? Social eng?
│                   │  Harmful content?"
│   PASS ──►        │
└───────────────────┘
        │
        ▼
┌───────────────────┐
│ SKILL/AGENT GUARD │ Code audit
│                   │ "Malicious patterns in skill
│                   │  code or agent config?"
│   PASS ──►        │
└───────────────────┘
        │
        ▼
┌───────────────────┐
│ COMMAND SHIELD    │ Deterministic (no LLM!)
│                   │ "rm -rf / ? curl | sh ?
│                   │  Pattern-matched instantly."
│   PASS ──►        │
└───────────────────┘
        │
        ▼
   ORCHESTRATOR

   ANY FAIL → message dropped, user notified
   Guards are INDEPENDENT — one crash ≠ all down
```

---

## 5. Memory System

```
CONVERSATION ENDS
      │
      ▼
┌─────────────────────────────────────────────────────────┐
│              AUTO-EXTRACTION (LLM pass)                 │
│                                                         │
│  Input: full conversation transcript                    │
│                                                         │
│  Output:                                                │
│    Fact:         "user is a developer"          c=0.95  │
│    Preference:   "prefers Python over JS"       c=0.88  │
│    Event:        "user deployed prod on 2026-03-10" c=0.72│
│    Relationship: "user works with team of 3"    c=0.65  │
└───────────────────────────┬─────────────────────────────┘
                            │
                            ▼
                     ┌─────────────┐
                     │   SQLite    │
                     │             │
                     │  FTS5       │◄── keyword search
                     │  sqlite-vec │◄── semantic search
                     └──────┬──────┘
                            │
                            ▼
              NEXT CONVERSATION: context assembly
              "fetch memories relevant to current query"
              → hybrid retrieval (FTS5 + vector, merged)
              → inject into system prompt
              → cross-channel: Telegram memories work on Discord
```

---

## 6. Model Routing: Priority + Weight

```
models.yml:
  - name: claude-opus      priority: 0  weight: 3
  - name: claude-sonnet    priority: 0  weight: 7
  - name: gpt-4o           priority: 1  weight: 5
  - name: local-ollama     priority: 2  weight: 1

REQUEST INCOMING:

Priority 0 group (claude-opus + claude-sonnet):
  ┌─────────────────────────────────────────┐
  │  Weighted random pick (30% Opus, 70% Sonnet)
  │  → claude-sonnet wins this spin          │
  │  → Request sent                          │
  │  → ✅ Success → done                     │
  │                                          │
  │  → ❌ Fail (timeout/error)               │
  │  → try claude-opus                       │
  │  → ❌ Fail again                         │
  │  → Priority 0 exhausted                  │
  └─────────────────────────────────────────┘
          │
          ▼ Priority 1 group (gpt-4o)
  → gpt-4o attempt
  → ❌ Fail
          │
          ▼ Priority 2 group (local-ollama)
  → ollama attempt
  → ✅ or ❌

Budget: monthly cap → soft (warn) or hard (refuse above limit)
```

**For Lyra**: `Anthropic API (P0) → Ollama Machine 2 (P1)` — maps directly.

---

## 7. ScalyClaw vs ClawFamily — Visual Map

```
COMPLEXITY / PRODUCTION-READINESS
↑
│                         ★ ScalyClaw
│                      (full platform, BullMQ)
│
│         ★ OpenFang
│      (Rust kernel, WASM)
│
│    ★ OpenClaw        ★ IronClaw
│ (framework)       (Rust resilience)
│
│  ★ NanoClaw
│ (security/simplicity)
│
│★ Nanobot            ★ MetaClaw / OpenClaw-RL
│(lean Python)          (learning plane)
│
└─────────────────────────────────────────────→ FEATURE BREADTH

Paperclip (control-plane, TypeScript) — off this axis, different layer
```

---

## 8. Skill Manifest: What "Language-Agnostic" Looks Like

```
skills/
  weather-skill/
    SKILL.md
    main.py

── SKILL.md ──────────────────────────────────────────
---
name: Weather
description: Get current weather for a city
script: main.py
language: python
---
Call this when the user asks about current weather
for a specific city or location.
──────────────────────────────────────────────────────

-- main.py --
import sys, requests
city = sys.argv[1]
r = requests.get(f"https://wttr.in/{city}?format=j1")
print(r.json()["current_condition"][0]["temp_C"] + "°C")
```

Worker auto-installs `requests` on first run. No `requirements.txt` needed. Hot-reload when SKILL.md changes. Deploy as `.zip` for portability.

---

## 9. Where ScalyClaw Sits in the Lyra Roadmap

```
TODAY (Lyra P0):
  asyncio.Queue (in-process)
  → good enough for 1 user, 1 machine
  → no Redis overhead

PHASE 2 (Machine 2 online):
  Redis + BullMQ pattern
  → ScalyClaw's Node/Worker split
  → Node on Machine 1 (Hub)
  → LLM-heavy workers on Machine 2 (AI Server)
  → same Redis, different machines

PATTERNS TO BORROW NOW (no Redis needed):
  1. Command Shield  → deterministic block list (0 latency, 0 cost)
  2. Priority+weight model routing  → Anthropic P0 → Ollama P1
  3. Auto-memory extraction at session close  → LLM pass on transcript

PATTERNS TO BORROW PHASE 2+:
  4. BullMQ job model for long-running tasks
  5. Worker isolation for code execution tools
  6. Zero-downtime reload via pub/sub
```

---

## 10. Key Takeaway

```
┌──────────────────────────────────────────────────────────────────┐
│                                                                  │
│  ScalyClaw = OpenClaw (framework) + Dashboard + BullMQ + Vault   │
│                                                                  │
│  It's what you'd build if you productized the ClawFamily.        │
│                                                                  │
│  Age: 14 days. Velocity: high.                                   │
│  Watch: if it hits 1k stars, it becomes the Home Assistant       │
│  of personal AI platforms.                                       │
│                                                                  │
│  For Lyra today: borrow 3 patterns, ignore the stack.           │
│  Command Shield + Model priority routing + Auto-memory extract.  │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
```
