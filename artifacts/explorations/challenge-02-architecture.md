# Challenge #2 — Architecture: asyncio Bus & Orchestration

> Challenge document based on knowledge base data.
> Last updated: 2026-03-02

---

## Our current plan

Hub-and-spoke with central `asyncio.Queue`:
- Channel adapters -> Queue -> Hub -> resolve_binding()
- Pools per (channel, user_id) with asyncio.Lock
- Isolated agents with dedicated workspaces

Inspired by NanoBot + OpenClaw.

---

## What the knowledge base brings

### 1. Mission Control — open-source dashboard for agent fleets

**Source**: [builderz-labs/mission-control](https://github.com/builderz-labs/mission-control)

Dashboard for orchestrating AI agent fleets:
- 26 monitoring panels (tasks, costs, logs, memory, webhooks, pipelines)
- Real-time updates via WebSocket/SSE
- Zero external dependencies (SQLite only)
- Quality gates and role-based access control

**Challenge**: our hub has no observability layer. In production, without monitoring, we are blind. Mission Control is a reference to study for the dashboarding layer.

### 2. Decapod — daemonless control plane

**Source**: [DecapodLabs/decapod](https://github.com/DecapodLabs/decapod)

Rust control plane called on demand by agents to:
- Align intent before inference
- Optimize context
- Enforce limits
- Produce completion proofs

**What this challenges**: our hub is a black box. Decapod proposes a model where the control plane is separated from the runtime and produces audit artifacts. Close to our hash-chained audit trail, but with stricter separation.

### 3. The 7 sins of agentic software

**Source**: [@ashpreetbedi, Twitter](https://x.com/ashpreetbedi/status/2026708881972535724)

Critical mistakes identified after 3 years of agent infrastructure in production:
1. Treating an agent like a script
2. Forcing request-response (stateless)
3. Ignoring persistence
4. Ignoring multi-tenancy
5. Confusing reasoning with execution
6. Demos hide the real infrastructure, state, costs and failure modes
7. No handling of intermediate failures

**What this challenges**: our pool with asyncio.Lock is stateful — good. But our handling of intermediate failures (skills that fail mid-pipeline) is not specified.

### 4. Harness Engineering — the new discipline

**Source**: [@charlierguo, Twitter](https://x.com/charlierguo/status/2026009225663750512)

OpenAI and Stripe teams are reorganizing into two roles:
- **Harness engineering**: building the environment where agents operate
- **Agent management**: directing agents within that environment

**What this brings**: our hub is exactly the "harness." This mental model is useful for clearly separating responsibilities in our code: the hub does not do AI, it does infrastructure.

### 5. OpenClaw as enterprise OS — lessons at 5B tokens

**Source**: [@MatthewBerman, Twitter](https://x.com/MatthewBerman/status/2026450191759585776)

After 5 billion tokens of OpenClaw usage:
- Email management, CRM, knowledge base, content pipeline
- Cron jobs, memory, financial tracking, logging, health pipeline
- OAuth security is a real problem (loophole resolved)

**What this challenges**: our hub's Phase 1 scope is minimalist (Telegram). OpenClaw shows that the real needs of a personal OS are much broader. Should we plan extension hooks from Phase 1?

---

## Recommendations

### Architecture (keep)
- Central asyncio.Queue: proven pattern, correct
- Pools with asyncio.Lock: good isolation
- Hub-and-spoke: validated by OpenClaw across millions of messages

### What we need to add

**From Phase 1:**
- Explicit handling of intermediate failures (retry, dead-letter queue)
- Structured logging of every message that traverses the hub (minimal observability)
- Per-message cost metrics from the start (you cannot optimize what you do not measure)

**Phase 2:**
- Observability layer (inspired by Mission Control — our own minimalist version)
- Extension hooks to add channels and skills without touching the core

### What we should not do
- Over-architect Phase 1 with complex Decapod/control plane
- Build a dashboard before having real traffic to observe

---

## Verdict

Our base architecture is solid. The real gap: **observability and failure handling**. These two points must be in the Phase 1 scope, even if minimal. The 7 sins of agentic software are an excellent checklist to run against our design.
