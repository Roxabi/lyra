# Architecture — Hub & asyncio Bus

> Lyra status: **P0 — Design in progress**
> Last updated: 2026-03-02

---

## Where we stand

Hub-and-spoke architecture with a central `asyncio.Queue` bus. Channel adapters (Telegram, Discord, Signal...) run as independent asyncio tasks, normalize messages into a unified `Message` format, and push into the queue. The hub consumes and routes.

**Routing**: bindings `(channel, user_id)` -> `(agent, pool_id)` + `asyncio.Lock` per pool to serialize requests from the same user.

**Phase 1**: single hub, single process, local. No distributed. No cluster.

---

## What we have in the knowledge base

### The 7 sins of agentic software (@ashpreetbedi)
> After 3 years of building agent infrastructure

1. **Treating an agent like a script** — no state, no persistence -> our bus with bindings is the right approach
2. **Forcing request-response** — asyncio.Queue + independent tasks avoids this
3. **Ignoring persistence** — the binding `(channel, user_id)` -> pool persists between messages
4. **Ignoring multi-tenancy** — each user_id = isolation via Lock
5. **Confusing reasoning with execution** — our Phase 2 architecture (atomic SLMs) addresses this
6. **Ignoring costs** — cloud LLM by default, local = fallback
7. **No monitoring** — to implement from P0

### Agent-Native Architectures (Dan Shipper, Every)
> 5 fundamental principles

1. **Parity** — the agent must be able to do everything a human can do through the channel
2. **Granularity** — atomic tools, no mega-tools
3. **Composability** — composable skills -> our skill system
4. **Emergent Capability** — skill combinations = new capabilities
5. **Improvement Over Time** — feedback loop -> procedural memory

### The Coding Agent Harness (Julian de Angelis, MercadoLibre)
> 20,000 developers, 4 key levers

1. **Custom rules** -> our CLAUDE.md / AGENTS.md
2. **Context engineering** -> CognitiveFrame (Phase 2)
3. **Context window management** -> compaction + 5-level memory
4. **Validation** -> guards on destructive actions

### Decapod (Rust, daemonless control plane)
> Interesting alternative to watch

Local control plane that agents call on demand to align intent, optimize context before inference, enforce limits, and produce completion proofs. Handles concurrent multi-agent execution.

**Question**: should we take inspiration from it for our hub? "Control plane separate from bus" pattern vs all-in-one.

### ClawRouter (smart LLM router, 70% savings)
> Local routing in <1ms across 14 dimensions, 30+ models

Inspiring for our Phase 2 routing: analyze the request before deciding which model executes it. 70-78% savings realistic.

---

## Challenges and open questions

### 1. Backpressure
**Problem**: if the hub is slow (local LLM busy), the queue grows. What behavior?
- Option A: reject + error message to user
- Option B: bounded queue with drop oldest
- Option C: wait + communicate ETA

**Decision to make** before P0.

### 2. Adapter failures
**Problem**: if the Telegram adapter crashes (network disconnection), what happens?
- Automatic asyncio task restart?
- Exponential backoff?
- Notification via another channel?

### 3. Unified Message format
The current dataclass works for text. But messages can be:
- Audio (Telegram voice)
- Images
- Documents/PDF
- Commands (/start, /restart...)

Is the `content: str | dict` field sufficient? Or do we need more typing?

### 4. Inter-machine scalability
Phase 1: everything on M1. Phase 2: M2 for heavy LLMs.
But if we have 10 simultaneous users on M1, and LLM requests go to M2 via HTTP, what is the pattern?
- Fire-and-forget + callback?
- Streaming HTTP (SSE)?
- The `asyncio.Lock` per pool already serializes, so max 1 LLM request/pool at a time.

### 5. Monitoring from the start
The 7 sins mention the absence of monitoring as a capital sin.
Minimal P0 metrics:
- Queue depth
- Message -> response latency
- Errors per channel
- M1 VRAM usage

Simple: Prometheus + Grafana? Or just structured logs to start?

---

## Decisions to make / POCs

| Decision | Options | Priority |
|----------|---------|----------|
| Backpressure strategy | reject / drop / wait | P0 |
| Error handling adapters | restart / notify | P0 |
| Message format v1 | str\|dict vs typed | P0 |
| Monitoring P0 | structured logs / prometheus | P0 |
| Control plane pattern | all-in-one vs Decapod-style | P1 |
| LLM routing M2 | streaming vs fire+callback | P1 |

---

## Risks

- **Context rot**: without compaction, the context grows -> quality degradation. Implement from P0.
- **Lock contention**: if a user makes 10 requests at once, all serialized. OK for personal use, problem for multi-tenant.
- **asyncio dependency**: if we need CPU-bound workers (embeddings, etc.), `asyncio.run_in_executor` or process pool.
