# AlphaClaw — Deep Analysis

> **URL**: https://github.com/chrysb/alphaclaw
> **Date**: 2026-03-13
> **Category**: Operations harness / DevOps wrapper for OpenClaw
> **Family**: ClawFamily (OpenClaw ecosystem — operations layer)
> **License**: MIT

---

## TL;DR

AlphaClaw is the **operations plane of the ClawFamily**. Where OpenClaw is the agent, Paperclip the control plane, MetaClaw the learning plane, and ScalyClaw a parallel product — AlphaClaw is the **DevOps wrapper**: setup wizard, self-healing watchdog, Git-backed rollback, prompt hardening, and browser-based observability. It makes OpenClaw deployable by non-terminal users and keeps it running without SSH rescue missions.

**Tagline**: *"The ultimate OpenClaw harness. Deploy in minutes. Stay running for months. No CLI required."*

---

## 1. Product Overview

### What It Is

A Node.js wrapper that spawns OpenClaw as a managed child process and wraps it with: a password-protected setup UI (Preact), a self-healing watchdog with crash-loop detection, automatic Git commits of the workspace, prompt hardening against agent drift, Google Workspace OAuth integration, webhook management, and one-click Railway/Render deployment.

### Core Problem Solved

OpenClaw is powerful but requires terminal expertise to deploy, configure, and keep running. AlphaClaw eliminates the ops burden: zero-to-production in one deploy, everything managed from the browser, self-healing when things crash, and anti-drift prompts to keep the agent disciplined.

### What Makes It Different

| vs. | AlphaClaw does |
|-----|----------------|
| Raw OpenClaw deployment | One-click Railway/Render deploy, guided setup wizard |
| Manual config file edits | Browser-based UI for all configuration |
| Manual crash recovery | Self-healing watchdog with auto-repair + notifications |
| Agent prompt drift | Anti-drift bootstrap prompts (`AGENTS.md`, `TOOLS.md`) injected every message |
| SSH-based file management | Browser file explorer with inline edits, diffs, Git sync |
| Manual workspace backups | Automatic hourly Git commits to GitHub |
| CLI-only channel pairing | One-click pairing from the Setup UI |
| No Google Workspace | Full OAuth flow for Gmail, Calendar, Drive, Docs, Sheets, Tasks, Contacts, Meet |

---

## 2. Tech Stack

| Layer | Technology |
|-------|-----------|
| **Runtime** | Node.js ≥ 22.12.0 |
| **HTTP** | Express |
| **Frontend** | Preact + htm + Wouter (no build step, CDN) |
| **Database** | SQLite (watchdog event log, webhook history) |
| **Gateway** | OpenClaw (spawned as child process on `127.0.0.1:18789`) |
| **Deployment** | Docker, Railway, Render |
| **Auth** | Password-based with exponential backoff brute-force protection |
| **OAuth** | Google Workspace (PKCE), OpenAI Codex (PKCE) |
| **Git** | Automatic hourly workspace commits via cron |
| **Language** | JavaScript (100%) |
| **Package** | `@chrysb/alphaclaw` on npm |

### No External Dependencies Beyond OpenClaw

No Redis, no message queue, no separate database server. Just Node.js + SQLite + the OpenClaw binary. This is by design — AlphaClaw is a thin wrapper, not a platform.

---

## 3. Architecture

### Process Model

```
┌─────────────────────────────────────────────────────────────┐
│                       ALPHACLAW                              │
│  (Express server — wraps everything)                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────┐   │
│  │ Setup UI │  │ Watchdog │  │ Webhooks │  │ Git Sync  │   │
│  │ (Preact) │  │ (health) │  │ (hooks)  │  │ (cron)    │   │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └─────┬─────┘   │
│       │              │              │               │         │
│  ┌────▼──────────────▼──────────────▼───────────────▼─────┐  │
│  │              Express Server + JSON APIs                 │  │
│  │              Auth · Proxy · Config                      │  │
│  └──────────────────────┬──────────────────────────────────┘  │
│                         │ proxy (127.0.0.1:18789)             │
└─────────────────────────┼─────────────────────────────────────┘
                          │
┌─────────────────────────▼─────────────────────────────────────┐
│                    OPENCLAW GATEWAY                             │
│  (child process — managed, monitored, restarted)               │
│  AI agent · Channels · Skills · Memory                         │
└─────────────────────────┬─────────────────────────────────────┘
                          │
┌─────────────────────────▼─────────────────────────────────────┐
│                  ALPHACLAW_ROOT_DIR (/data)                     │
│  .openclaw/ · .env · logs · SQLite · workspace files           │
└───────────────────────────────────────────────────────────────┘
```

### Key Pattern: Reverse Proxy + Child Process

AlphaClaw is NOT a fork or modification of OpenClaw. It:
1. Spawns OpenClaw as a child process
2. Proxies traffic to it
3. Monitors its health
4. Restarts it when needed
5. Adds UI/config/webhook layers on top

**Zero lock-in**: remove AlphaClaw, OpenClaw keeps running. Nothing proprietary.

---

## 4. Key Systems Deep-Dive

### 4.1 Setup UI (Dashboard)

Password-protected web dashboard with 7 tabs:

| Tab | Purpose |
|-----|---------|
| **General** | Gateway status, channel health, pending pairings, Google Workspace, repo sync schedule |
| **Browse** | File explorer — visibility, inline edits, diff view, Git-backed sync |
| **Usage** | Token summaries, per-session and per-agent cost/token breakdown |
| **Watchdog** | Health monitoring, crash-loop status, auto-repair toggle, event log, live log tail |
| **Providers** | AI credentials (Anthropic, OpenAI, Gemini, Mistral, Voyage, Groq, Deepgram) + model selection |
| **Envars** | Environment variable editor with gateway restart prompts |
| **Webhooks** | Webhook endpoints, transform modules, request history, payload inspection |

Tech: Preact + htm + Wouter — no build step, served directly. Lightweight.

### 4.2 Watchdog

Self-healing process monitor:

| Capability | Mechanism |
|-----------|-----------|
| **Health checks** | Periodic `openclaw health` with configurable interval |
| **Crash detection** | Listens for gateway exit events |
| **Crash-loop detection** | Threshold-based (default: 3 crashes in 300s) |
| **Auto-repair** | `openclaw doctor --fix --yes` → relaunch gateway |
| **Notifications** | Telegram + Discord alerts for crashes, repairs, recovery |
| **Event log** | SQLite-backed incident history with API and UI access |

This is the most production-critical feature. Without it, a crashed OpenClaw needs manual SSH intervention.

### 4.3 Prompt Hardening (Anti-Drift)

Ships bootstrap prompts (`AGENTS.md`, `TOOLS.md`) injected into the agent's system prompt on **every message**. These enforce:
- Safe practices
- Commit discipline
- Change summaries
- Tool usage patterns

Combined with Git Sync, every agent action is version-controlled and auditable.

### 4.4 Git Sync

Automatic hourly commits of the OpenClaw workspace to GitHub:
- Configurable cron schedule
- CLI command: `alphaclaw git-sync -m "message"`
- Combined with prompt hardening → full audit trail of agent actions

### 4.5 Google Workspace Integration

Full OAuth integration (not just API keys):
- Gmail, Calendar, Drive, Docs, Sheets, Tasks, Contacts, Meet
- Guided Gmail watch setup with Google Pub/Sub topic, subscription, and push endpoint
- All configured from the UI — no manual OAuth dance

### 4.6 Webhooks

Named webhook endpoints with:
- Per-hook transform modules (JavaScript transforms)
- Request logging
- Payload inspection
- Query-string token support for providers without header auth
- Gmail watch delivery flows

### 4.7 Channel Orchestration

- Telegram + Discord bot pairing from the UI
- Credential sync
- Guided wizard for splitting Telegram into multi-threaded topic groups
- First CLI pairing auto-approved (subsequent appear in UI)

---

## 5. Feature Matrix

| Feature | Status |
|---------|--------|
| One-click deploy (Railway/Render) | ✅ |
| Setup wizard (guided onboarding) | ✅ |
| Password-protected dashboard | ✅ |
| Self-healing watchdog | ✅ |
| Crash-loop detection + auto-repair | ✅ |
| Telegram/Discord notifications | ✅ |
| Browser file explorer + inline edits | ✅ |
| Automatic Git workspace sync | ✅ |
| Anti-drift prompt hardening | ✅ (unique in family) |
| Google Workspace OAuth (8 services) | ✅ |
| Webhook management + transforms | ✅ |
| Channel pairing from UI | ✅ |
| Telegram topic groups wizard | ✅ |
| Token usage tracking | ✅ |
| Provider credential management | ✅ (7 providers) |
| Environment variable editor | ✅ |
| Version management (self-update) | ✅ |
| Codex OAuth (PKCE) | ✅ |
| Docker deployment | ✅ |
| npm package | ✅ `@chrysb/alphaclaw` |
| Test suite | ✅ 90 tests |

---

## 6. Business Model & Positioning

### Current
- **MIT licensed, fully self-hosted** — no SaaS tier, no paid features
- npm package: `@chrysb/alphaclaw`
- One-click deploy buttons for Railway and Render
- Docker-first deployment model

### Positioning
- **Target**: OpenClaw users who want production reliability without ops expertise
- **Value prop**: "First deploy to first message in under five minutes"
- **Philosophy**: wraps, doesn't fork — zero lock-in, eject anytime

### Not For
- Non-OpenClaw agents (tightly coupled to OpenClaw gateway)
- Users who want a complete agent platform (use ScalyClaw instead)
- macOS local development (Docker/Linux only)

---

## 7. GitHub Metrics (2026-03-13)

| Metric | Value |
|--------|-------|
| Stars | **639** |
| Forks | 79 |
| Language | JavaScript (100%) |
| Created | 2026-02-25 (~2.5 weeks ago) |
| Last pushed | 2026-03-13 (today — actively maintained) |
| License | MIT |
| Package | `@chrysb/alphaclaw` (npm) |
| Tests | 90 tests (unit + watchdog suite) |
| Node.js | ≥ 22.12.0 |

**Notably popular for its age** — 639 stars in ~2.5 weeks. This is by far the most-starred project in the ClawFamily after OpenClaw itself. The operations/DevOps angle clearly resonates with users who want OpenClaw but struggle with deployment.

---

## 8. Key Design Decisions

| Decision | Why It Matters |
|----------|---------------|
| **Child process, not fork** | Zero coupling — OpenClaw is untouched. AlphaClaw can be removed without migration. Updates to OpenClaw are independent. |
| **No build step for UI** | Preact + htm loaded from CDN. No webpack, no Vite. Fast to iterate, trivial to deploy. |
| **SQLite for event log** | No Redis, no Postgres. Single-file database, zero-config. Fits the "thin wrapper" philosophy. |
| **Prompt hardening on every message** | Not a one-time config — injected per-message to prevent drift. Agent can't "forget" discipline. |
| **Git sync as first-class feature** | Every agent action is auditable. Combined with prompt hardening (commit discipline), creates a full paper trail. |
| **Password auth, not pairing codes** | Simpler than OpenClaw's flow. Trade-off: less secure, but fits the "everything in browser" philosophy. |
| **Auto-approve first CLI pairing** | Eliminates the chicken-and-egg problem of needing CLI to approve CLI. |
| **Docker-first, no macOS** | Picks a lane — production Linux, not local dev. Simplifies the matrix. |

---

## 9. Security Trade-offs

AlphaClaw **intentionally trades some security for ease of setup**. The README is transparent about this:

| Area | AlphaClaw | vs. Raw OpenClaw |
|------|-----------|-----------------|
| Auth | Single password (brute-force protected) | Pairing code flow |
| Channel pairing | UI click (anyone with password) | CLI-only |
| First CLI pairing | Auto-approved | Manual approval |
| Query-string tokens | Supported (for limited providers) | Not supported |
| Gateway token | Auto-generated, lives in `.env` | Manual setup |

**Bottom line**: fine for personal/small-team use. Not for enterprise multi-tenant without hardening.

---

## 10. ClawFamily Comparison

### Position in the Ecosystem

```
┌─────────────────────────────────────────────────────────────┐
│                      PAPERCLIP                               │
│  Control-plane: org charts, goals, budgets, multi-agent      │
└──────────────────┬──────────────────────────────────────────┘
                   │ orchestrates
┌──────────────────▼──────────────────────────────────────────┐
│                      OPENCLAW                                │
│  The agent — executes tasks, uses tools                      │
└──────┬───────────────────┬──────────────────────────────────┘
       │ wrapped by         │ proxied by (optional)
┌──────▼───────┐    ┌──────▼──────────────────────────────────┐
│  ALPHACLAW   │    │              METACLAW                    │
│  Ops-plane:  │    │  Learning-plane: intercepts, scores,     │
│  deploy,     │    │  trains LoRA                             │
│  watchdog,   │    └─────────────────────────────────────────┘
│  UI, Git     │
└──────────────┘
         ╔══════════════════════════════════════════╗
         ║              SCALYCLAW                   ║
         ║  Complete product: 7 channels + workers  ║
         ║  (parallel track, not layered on others)  ║
         ╚══════════════════════════════════════════╝
```

AlphaClaw is the **operations layer** on OpenClaw — it doesn't replace it, it makes it deployable and reliable.

### Head-to-Head

| Dimension | OpenClaw | AlphaClaw | ScalyClaw | IronClaw |
|-----------|---------|-----------|----------|---------|
| **Type** | Agent framework | Ops wrapper | Complete product | Prod-hardened |
| **Role** | Executes tasks | Deploys + monitors | Everything built-in | Scaling focus |
| **Dashboard** | ❌ | ✅ (Preact) | ✅ (React 19) | ? |
| **Watchdog** | ❌ | ✅ self-healing | ❌ (workers self-restart) | ? |
| **Prompt hardening** | ❌ | ✅ per-message | ❌ | ❌ |
| **Git audit trail** | ❌ | ✅ auto-sync | ❌ | ❌ |
| **Google Workspace** | ❌ | ✅ full OAuth | ❌ | ❌ |
| **One-click deploy** | ❌ | ✅ Railway/Render | ❌ | ❌ |
| **Lock-in** | N/A | Zero (eject anytime) | Full product | Full product |
| **Stars** | ~2000+ | 639 | 10 | ? |

---

## 11. Relevance to Lyra / 2ndBrain

### Direct Alignments

| AlphaClaw concept | Lyra equivalent | Notes |
|------------------|----------------|-------|
| Watchdog (crash detection + auto-repair) | supervisord | Lyra already uses supervisord for process management. AlphaClaw's watchdog pattern adds crash-loop detection and auto-repair (`doctor --fix`) on top — supervisord doesn't auto-repair, only restarts. |
| Prompt hardening (anti-drift, per-message) | Not implemented | **Interesting pattern for Lyra.** Injecting discipline prompts per-message prevents agent drift over long sessions. Lyra could enforce tool discipline, memory hygiene, etc. |
| Git workspace sync (automatic commits) | Not implemented | Lyra's session transcripts are JSONL files. Periodic Git commits of the workspace (memory state, config changes) would create an audit trail. |
| Setup UI (browser-based config) | Not planned yet | Lyra has no admin UI. If/when needed, Preact+htm (no build step) is a smart minimal approach. |
| Google Workspace OAuth | Not implemented | Lyra has Google integration via separate skills (google-tasks, google-drive, agenda-recap). AlphaClaw's unified OAuth is cleaner but tightly coupled to its UI. |
| Webhook management | Not implemented | Could be useful for Lyra to receive external events (GitHub webhooks, Gmail push notifications). |
| Telegram topic groups | Partially — 2ndBrain has single-chat bot | AlphaClaw's topic group wizard could inspire Lyra's Telegram adapter to support multi-topic routing. |

### What Lyra Can't Directly Use

| AlphaClaw feature | Why |
|------------------|-----|
| Gateway manager (child process) | AlphaClaw is OpenClaw-specific. Lyra is its own agent, not a wrapper. |
| One-click deploy buttons | Lyra is self-hosted on local hardware, not cloud PaaS. |
| Express + Preact stack | Lyra is Python asyncio. Different ecosystem entirely. |
| npm distribution | Lyra doesn't ship as a package (yet). |

### Key Borrow: Prompt Hardening Pattern

The most transferable idea is **anti-drift prompt injection**:

```python
# Lyra adaptation (Python)
DISCIPLINE_PROMPT = """
## Agent Discipline (injected every turn)
- ALWAYS check memory before answering from knowledge
- NEVER execute shell commands without user confirmation
- ALWAYS summarize actions taken at end of turn
- If uncertain, ASK — don't guess
"""

async def build_system_prompt(base_prompt: str, context: dict) -> str:
    """Inject discipline rules into every LLM call."""
    return f"{base_prompt}\n\n{DISCIPLINE_PROMPT}\n\n{context_section(context)}"
```

This is simple but addresses a real problem: agents drift from their instructions over long conversations.

### Key Borrow: Crash-Loop Detection

supervisord restarts on crash but has no crash-loop awareness. AlphaClaw's pattern:

```python
# Lyra adaptation
class CrashLoopDetector:
    def __init__(self, threshold=3, window_seconds=300):
        self.crashes: list[float] = []
        self.threshold = threshold
        self.window = window_seconds

    def record_crash(self) -> bool:
        """Returns True if crash-loop detected."""
        now = time.time()
        self.crashes = [t for t in self.crashes if now - t < self.window]
        self.crashes.append(now)
        return len(self.crashes) >= self.threshold

    async def handle_crash_loop(self):
        """Enter repair mode instead of blindly restarting."""
        await run_doctor_fix()
        await notify_telegram("Crash-loop detected. Auto-repair attempted.")
```

---

## 12. Risks & Concerns

| Risk | Severity | Notes |
|------|----------|-------|
| Tight coupling to OpenClaw | High | Only works with OpenClaw. If OpenClaw's API/CLI changes, AlphaClaw breaks. |
| Very early (2.5 weeks old) | High | Despite 639 stars, no production track record. APIs will change. |
| JavaScript-only | Medium | No TypeScript — harder to maintain at scale. |
| Security trade-offs | Medium | Intentionally weaker auth model. Fine for personal use, risky for teams. |
| Docker/Linux only | Medium | No macOS local dev. Limits contributor base. |
| Single-process model | Low | Express server + child process. No horizontal scaling. Fine for personal use. |
| No memory/AI features | Low | Pure ops wrapper — no memory, no intelligence. That's OpenClaw's job. |

---

## Summary

AlphaClaw is the **operations layer of the ClawFamily** — the first project to solve the "OpenClaw is powerful but hard to deploy and keep running" problem. Its 639 stars in 2.5 weeks confirm strong demand for this category.

Its most novel contributions are:
1. **Prompt hardening** — anti-drift bootstrap prompts injected per-message, ensuring agent discipline doesn't degrade over time
2. **Self-healing watchdog** — crash-loop detection + auto-repair, not just blind restarts
3. **Zero-lock-in philosophy** — wraps without forking, eject anytime

It's not an agent, not a framework, not a platform — it's the **DevOps glue** that makes OpenClaw production-ready for people who don't want to SSH into servers.

**Top 3 borrows for Lyra**:
1. **Prompt hardening** — per-message discipline injection to prevent drift
2. **Crash-loop detection** — upgrade supervisord restarts with loop awareness + auto-repair
3. **Telegram topic groups** — multi-topic routing pattern for the Telegram adapter
