# Harness Epic — Consolidated Architecture & Resume Point

> **Status:** drafted. Epic created on GitHub: [#1490](https://github.com/Roxabi/lyra/issues/1490).
> **Owner:** mickael
> **Last touched:** 2026-05-20
> **Supersedes:**
> - `artifacts/analyses/archive/new-harness-epic-context.md` (resume point, 2026-05-17)
> - `artifacts/analyses/archive/harness-context-ownership.md` (brainstorm, 2026-05-17)
>
> **Purpose:** single source for the harness epic until it lands on GitHub. Captures the architectural reframe, the closed-out trajectory, what shipped on staging that changes the picture, the open decisions, and the practical reference data needed to resume drafting in one pass.

---

## TL;DR

We pivoted to a clean, long-term, distributed Lyra harness. Two stale epics (`#987`, `#633`) and 8 children were closed `not planned`. A flurry of staging work between 2026-05-14 and 2026-05-17 removed prerequisites (NatsLlmClient migration, NatsLlmDriver deletion, LiteLLMDriver, render v2 cutover, JetStream JOBS substrate scaffolding).

**Architectural reframe — the centerpiece:** the "should history live in hub or harness?" question conflates **three** distinct concerns. Split them:

| Concern | Owner |
|---|---|
| Canonical store (durable history) | **Shared persistent layer** (Redis / JetStream KV, target per `#640`) |
| Context builder (assemble `messages[]` per turn) | **Hub** |
| Turn-local working memory (tool intermediates, partial msgs) | **Harness** |
| Write-back (new turn → canonical store) | Hub (single writer) |
| Wire transport | inline ≤256 KB, else JetStream object-store ref (`#1061` dep) |

Net: harness becomes **pure compute** — no `pool_id` affinity, NATS queue-group scales horizontally, sub-harness spawn (Q2) is trivial. Runtime (LangGraph / custom / Hermes) becomes swappable behind one interface.

**Next concrete action when resuming:** confirm the 3-concerns split, answer the remaining open questions (§6), confirm naming (§8), pick a runtime (§9). One pass produces the epic body + child issue list.

---

## 1. Trajectory — how we got here

```
1. Investigation question (user):
   "Why does a bot wired to llmCLI only (no clipool) fail to display tool usage?"
   → Found: legacy NatsLlmDriver read chunk["text"], llmCLI emits chunk["delta"].
     Wire-format mismatch. Tool-use chunks not emitted by llmCLI by design.

2. Investigation pivot (user):
   "Nevermind, we said llmCLI runs through a harness like clipool — do we have
    anything started around that?"
   → Found two epics covering the same surface:
       #987  feat: deprecate Anthropic API — llmCLI NATS backend + HarnessCLI
       #633  epic(arch): lyra_harness — agentic envelope as standalone NATS service
     Both partially-specified, overlapping, framed around the Anthropic OAuth
     incident rather than long-term architecture.

3. User decision:
   "Our priority is to have a global long term architecture for that and scalable."
   "Deprecate both epic and create a new one clean plz (deprecate children also,
    we will create new later today)"
   → Closed #987, #633 + all children with `not planned` (see §5).

4. Side-step (user): "Regarding 445, what is done or not?"
   → Refreshed status of #445 children. Closed #628 (clipool ref impl shipped
     under different paths), #584 (LLM offloading shipped).

5. Architectural deep dive:
   Reframed the hub-vs-harness debate as three concerns (canonical store,
   context builder, turn-local memory). Recommended ownership split. Identified
   runtime candidates (LangGraph, Goose, Hermes, Pi).

6. Drafting blocked on intent decisions.
```

---

## 2. Intent & target — draft language for the new epic

> **What we're building:**
> A stateless NATS worker — **`lyra_harness`** — that executes one agent turn end-to-end. The hub assembles `messages[]` per turn (system prompt + tools + agent_config + history slice + new user message) and ships it. The worker runs the agentic loop (LLM call → tool execution → LLM call → … → final response), emits a stream of `LlmEvent`s back to the hub, and terminates. The worker holds **no per-pool state** between turns.

> **Why we're building it:**
> Long-term scalable distributed Lyra. Today the hub embeds the agent runtime; the hub process is the bottleneck for horizontal scale, fault isolation, and language polyglot (Python today, Rust/Go tomorrow). Pushing the agent turn to a stateless worker:
> 1. Makes the hub a thin router (aligns with `#640` M3 statelessness)
> 2. Enables horizontal scale (N workers behind one queue group)
> 3. Per-turn isolation (one bad turn = one worker, not a hub crash)
> 4. Substrate for the worker fleet (`#1044`) and Phase-3 SLMs (`#61`)
> 5. Runtime swappability (LangGraph → custom → Hermes touches only the harness)

> **Why now:**
> All architectural prerequisites have landed on staging in the last 7 days. The remaining work is the harness itself — no more "first let me migrate X" debt.

> **Out of scope (initial epic):**
> - Multi-turn pre-emption / cancellation mid-turn
> - Cross-worker session affinity (any worker handles any pool_id by design)
> - Custom inference backends inside the harness (delegates to `clipool` / `NatsLlmClient` / `LiteLLMDriver`)
> - GPU-aware routing (covered by future `#603` load-aware routing)
> - Canonical-store backend choice (Redis vs JS-KV) — defers to `#640`

---

## 3. Architectural reframe — three concerns, not two locations

The hub-vs-harness debate confuses three distinct things. Treating them separately makes the answer obvious.

### 3.1 The three concerns

**Concern #1 — Canonical store (durable history).** The system of record for every turn ever spoken in conversation `pool_id`. Survives all process restarts. Read by audit, reply-routing, memory eviction policy, multi-channel coherence.
- Today: `TurnStore` (SQLite, hub-side).
- Target (`#640`): Redis / JetStream KV.

**Concern #2 — Context builder.** The function that, **per turn**, produces the `messages[]` array (plus system prompt and tool list) to ship to the LLM. Pulls from canonical store, applies policy (pruning, summarisation), injects memory (semantic/working/episodic), prepends system prompt, attaches tool descriptions.
- Today: delegated to claude-cli's `--resume` (provider-owned context). Lyra contributes only a system prompt and the new user message.
- Tomorrow: must move into Lyra — LiteLLM / local models have no provider-side session API; harness needs to know what context to send; memory injection needs to happen *somewhere visible*.

**Concern #3 — Turn-local working memory.** State that exists only during one turn: parsed tool calls, intermediate results, partial assistant message under construction. Discarded after the turn completes.
- Today: lives inside claude-cli process.
- Tomorrow: lives inside whatever runs the agentic loop (the harness).

### 3.2 Recommended ownership

| Concern | Owner | Rationale |
|---|---|---|
| Canonical store (#1) | **Shared persistent layer** (Redis / JS-KV per `#640`) | M4 trajectory already there; neither compute layer is stateful |
| Context builder (#2) | **Hub** | Only component with full Lyra context: memory, agent config, system prompt, multi-channel merging, auth, tool permissions per agent |
| Turn-local working memory (#3) | **Harness** | Naturally ephemeral; lives in the loop that uses it |
| Write-back | **Hub** (single writer) | No concurrency mess; matches today's TurnStore pattern |
| Wire transport | inline ≤256 KB, else `history_ref` | Avoids NATS max-msg blowups; reuses `#1061` object-store work |

### 3.3 Why this beats the pure options

| | Hub-owns-all (A) | Harness-owns-all (B) | Shared-store (C) | **3-concerns split (★)** |
|---|---|---|---|---|
| Hub stateless | ✗ (holds canonical) | ✓ | ✓ | ✓ |
| Harness stateless | ✓ | ✗ (sticky routing) | ✓ | ✓ |
| Sub-harness spawn cheap | ✓ | ✗ (needs snapshot) | ✓ | ✓ |
| Crash recovery free | ✓ | ✗ | ✓ | ✓ |
| Multi-channel coherence | ✓ | ✗ | ✓ | ✓ |
| Memory injection clean | ✓ | ✗ (inversion) | △ | ✓ |
| Runtime swappable | △ | ✗ (locks state) | △ | ✓ |
| Wire payload size | high | tiny | medium | bounded (two-tier) |
| Aligns with `#640` | △ | ✗ | ✓ | ✓ |

The split combines hub-owned policy wins (memory, system prompt, multi-channel) with shared-store wins (M4-ready, crash recovery, statelessness). Goose-style runtimes that want to own #1+#2 collide with hub's existing ownership — Goose has `override_conversation` precisely for this, but we'd fight it every turn. LangGraph's `MemorySaver` (stateless-per-call) is the cleaner fit; runtime-agnostic is easiest.

### 3.4 Data-flow diagram (target state)

```
┌──────────────────── canonical store ────────────────────┐
│   Redis / JetStream KV  (target per #640)                │
│   • full conversation history, keyed by pool_id          │
│   • semantic memories, agent prefs, pairings             │
│   • single source of truth                               │
└──────────────────────────────────────────────────────────┘
                  ▲                          ▲
                  │ read/write (hub only)    │ read-only slice (sub-harness)
                  │                          │
┌─── hub ─────────┴────────────┐    ┌──── harness ──────────────┐
│  context builder              │    │  agentic loop runtime      │
│  • read history from store    │    │  • LangGraph / custom /    │
│  • inject memory              │    │    Hermes (swappable)      │
│  • assemble system prompt     │───▶│  • parsed tool calls       │
│  • attach tool descriptions   │    │  • intermediate results    │
│  • ship LlmRequest            │    │  • dies at end of turn     │
│  • write new turn back        │◀───│  • returns updated history │
│    after harness reply        │    │    + assistant message     │
└───────────────────────────────┘    └────────────────────────────┘
```

### 3.5 Wire shape (concrete)

```python
class HarnessRequest(BaseModel):
    request_id: str
    pool_id: str
    history: list[Message] | HistoryRef     # inline ≤256 KB else by-ref
    system_prompt: str
    tools: list[ToolDescriptor]
    model: ModelConfig
    metadata: dict                          # memory hints, tracing, etc.

class HarnessResponse(BaseModel):
    request_id: str
    assistant_message: Message
    updated_history_delta: list[Message]    # turns to append to canonical store
    tool_calls_summary: list[ToolUseSummary]
    duration_ms: float
    error: ErrorInfo | None
```

Harness is stateless: every turn carries everything it needs to run. No `pool_id`-keyed local state.

---

## 4. What shipped 2026-05-14 → 2026-05-17 (changes the plan)

### Big-deal landed

| # | What | Impact on harness epic |
|---|---|---|
| **#1119** | NatsLlmClient — full `LlmProvider` conformance + hub bootstrap migration | `LlmProvider` contract is **stable** and proven over NATS. Harness can directly target `LlmProvider` as outbound LLM port — no new interface needed. |
| **#1206** | `NatsLlmDriver` **deleted**, renamed at all wiring sites | No more "migration debt" framing. Clean slate. |
| **#665** | `LiteLLMDriver` — `LlmProvider` wrapping the `litellm` lib | Harness has a **2nd inference target day-1** (claude-cli + litellm). Tool-use across both testable from the start. |
| **#663** | epic(llm): replace AnthropicSdkDriver with LiteLLMDriver | Stale Q4 input removed. |
| **#1102** / **#1205** | Drop v1 render events; v2-only path | No more v1 fallback. Harness emits/consumes v2 events only. |
| **#1214** | Rebuild tool recap card on v2 `ToolCall*` events | Confirms full UX → v2 wiring. Harness emits standard `LlmEvent`; `StreamProcessor` handles the rest. |
| **#1211** | v2 tests rewrite for B7+B8 skipped paths | v2 path has integration coverage. |

### Newly opened (changes the plan)

| # | What | Why it matters |
|---|---|---|
| **#1203** | JetStream `JOBS` stream + DLQ — bootstrap for `lyra.jobs.*` bus | **Changes Q3 transport**: JetStream substrate is now sunk cost. Object-store-by-ref tier becomes materially cheaper. |
| **#1198** | `#665` backend-enum cleanup tail | **Touches Q4**: backend enum will be edited anyway — schedule with harness backend addition. |
| **#1201** | refactor(llm): make `stream()` a true async generator across `LlmProvider` + 4 drivers | Harness `LlmProvider` impl must conform. Land before / alongside harness. |
| **#1199** / **#1200** | `WorkerError` runtime KNOWN_CODES guard + truncation/scrubbing | **Affects Q5**: canonical error vocab still being tightened. Harness consumes post-#1199/#1200 vocab. |
| **#1181** / **#1182** / **#1183** | `lyra.event.*` + `lyra.metric.*` taxonomy + producers + retention | Harness must emit `lyra.event.*` from day-1; observability is no longer a follow-up. |
| **#1212** / **#1215** | sanitize `str(exc)` in `error_text` + `WorkerError.message` (bus-visible info-leak) | Harness must follow the same sanitization pattern. |

### Worker fleet (peer epic `#1044`) — naming convention locked-in

| Layer | Pattern | Example for harness |
|---|---|---|
| Container | `lyra-<role>` | `lyra-harness` |
| Quadlet file | `lyra-<role>.container` | `lyra-harness.container` |
| NATS identity | `<role>` | `harness` |
| nkey secret | `lyra-nkey-<role>` | `lyra-nkey-harness` |
| Make target | `make <role>` | `make harness` |
| Env file | `%h/.lyra/env/<role>.env` | `harness.env` |
| Inbox (auto-derived per ADR-064) | `_inbox.<role>.>` | `_inbox.harness.>` |
| Heartbeat subject | `lyra.<role>.heartbeat` | `lyra.harness.heartbeat` |

---

## 5. Closed in this session — full list

All closed between 2026-05-14 and 2026-05-17.

### Stale harness epics + children (10 issues, `not planned`)

| # | Title | State |
|---|---|---|
| #987 | feat: deprecate Anthropic API — llmCLI NATS backend + HarnessCLI | superseded by NEW EPIC |
| #988 | feat: create roxabi-harness — HarnessCLI standalone repo | superseded |
| #990 | feat(clipool): harness-cli backend + hub routing logic | superseded |
| #633 | epic(arch): lyra_harness — agentic envelope as standalone NATS service | superseded by NEW EPIC |
| #635 | feat(hub): serialize history[] into harness wire format | superseded |
| #636 | feat(llm): NatsHarnessClient — hub-side LlmProvider for stateless harness | superseded |
| #637 | feat(infra): harness_adapter_standalone.py — stateless agentic loop + tool exec | superseded |
| #638 | feat(infra): lyra_harness supervisor program + bootstrap wiring | superseded |
| #639 | test: integration test — stateless harness round-trip with tool execution | superseded |
| #672 | feat(harness): wire lyra_harness → OTelObsProvider | superseded |

### `#445` children verified done (clipool reference impl already shipped)

| # | Title | Evidence |
|---|---|---|
| #629 | NatsCliClient — LlmProvider over NATS for Claude CLI | `src/lyra/llm/drivers/cli_nats.py:53` (`CliNatsDriver`) |
| #630 | cli_adapter_standalone.py — NATS wrapper around CliPool | `src/lyra/adapters/clipool/clipool_worker.py:103` + `bootstrap/standalone/clipool_standalone.py:18` |
| #631 | lyra_cli supervisor program + bootstrap wiring | `deploy/quadlet/lyra-clipool.container` (Quadlet replaced supervisor) |
| #632 | integration test — NATS CLI round-trip streaming | descoped to `tests/llm/drivers/test_cli_nats_driver.py` (972 lines) |
| #628 | **epic** lyra_cli standalone | 4/4 children resolved |
| #584 | **epic** Machine 2 LLM offloading | 5/5 done + bonus #1104/PR #1121/llmCLI #12 shipped |

### Deliberate divergences from original specs (defaults for new harness epic)

| What | Original spec | Actual shipped |
|---|---|---|
| Class name (LLM client) | `NatsLlmDriver` | `NatsLlmClient` (driver deleted in `#1206`) |
| Class name (CLI driver) | `NatsCliClient` | `CliNatsDriver` |
| Deploy unit | supervisor `*.conf` | Podman Quadlet `*.container` |
| Env-var gate | `LYRA_NATS_LLM=1` | absent — bootstrap checks `NATS_URL` presence |

---

## 6. Decision status — what's answered, what's open

| # | Topic | Status | Recommendation |
|---|---|---|---|
| Q1 | Tool execution layer (`#493` `ToolHandler` vs internal vs hybrid) | **open** | Lean A — co-design with `#1047` `JobHandler` registry to avoid drift |
| Q2 | Tool-execution locality (all in-harness vs FS-only vs per-tool routed) | **open** | Lean A for v1 — harness owns tool execution; `#1048` shape (proxy-back-over-NATS for remote capabilities) is the escape hatch we'd add later |
| Q3 | Wire/history transport (inline cap vs hybrid vs always object-store) | **provisional B (hybrid)** | 3-concerns reframe answers this: hub builds `messages[]`; inline ≤256 KB else `history_ref` via `#1061` object-store substrate. `#1203` JetStream JOBS makes always-by-ref (C) materially cheaper too — could re-discuss. |
| Q4 | Backend taxonomy (`agent_config.backend`) | **answered: A** (per prior intent) | Collapse to `claude-cli \| harness`; litellm/ollama become harness *models*. Schedule with `#1198` cleanup-tail. |
| Q5 | Failure semantics | **answered: B** (per prior intent) | Tool-error-soft — `ToolUseLlmEvent` raises → `ToolResultLlmEvent { is_error: true }`, loop continues. Matches Claude/OpenAI agentic SDK behavior. |
| — | Ownership split (3-concerns) | **needs explicit confirmation** | §3 — hub owns context-builder, harness owns turn-local, shared store owns canonical |
| — | Runtime choice (LangGraph vs custom vs Hermes) | **answered: Custom** | Custom thin loop (~200 lines Python) — zero external lock-in |
| — | Sub-harness spawn (Q2 sibling) in v1 or follow-up? | **answered: follow-up** | Trivial under 3-concerns model, but adds scope to v1 |
| — | Canonical-store backend (Redis vs JS-KV) | **deferred to `#640`** | Harness epic stays silent — store is just a client dependency |
| — | Naming (§8) | **answered: OK** | Aligned with `#1044` convention |
| — | Memory injection | **answered: Hub pre-builds** | Memory = external tool (roxabi-cortex); no harness calls |
| — | Streaming | **answered: Real-time** | `HarnessTurnEvent` emitted as loop runs |
| — | Tool result shape | **answered: Structured dict** | `{"output": ..., "status": ..., "artifacts": [...]}` |
| — | `HistoryRef` resolver | **answered: Blobstore** | Reuse #1330 V8 HTTP-fronted blobstore |
| — | `LyraToolProxy` subject | **answered: Reuse `lyra.jobs.*`** | Worker fleet already uses this; tool = job |
| — | Turn timeout | **answered: None** | Healthcheck only; no per-turn timeout |

---

## 7. Implications for existing systems

| Component | Today | Under harness epic |
|---|---|---|
| `TurnStore` | Audit-only, SQLite hub-side | Stays audit, or merges into canonical store (TBD, defer to `#640`) |
| `MemoryManager` | Hub-side | Stays hub-side; queried during context-builder step. No call inversion. |
| `MessageIndex` | Hub-side (reply-to routing) | Stays hub-side. Independent of harness. |
| `CliPool` / `clipool_worker` | Backend = `claude-cli` (in-proc CliPool) | Becomes one of N inference targets behind `LlmProvider`. Decision: defer; reassess after harness lands. |
| `_VALID_BACKENDS` | `{"claude-cli", "ollama", "litellm"}` | Per Q4=A → `{"claude-cli", "harness"}`. Schedule with `#1198`. |
| `_shared_streaming_emitter.py` / `StreamingSession` | Tool events via `ToolSummaryRenderEvent` | Stays. v2 typed splits (`#1102`) compatible — harness emits same `RenderEvent` types. |
| `roxabi-contracts` | `LlmRequest` / `LlmChunkEvent` / `LlmResponse` | Add `HarnessRequest` / `HarnessResponse` envelopes. Existing LLM envelopes stay for raw-LLM path. Harness layer sits *above* the LLM layer. |
| `#640` (hub statelessness M4) | Open | Compatible. Hub becomes router + context-builder + store-client. Its "state" is the shared store. |

---

## 8. Naming — confirm or amend

```
service binary    : lyra_harness          (Quadlet unit + supervisor target)
role/queue group  : harness-workers       (matches `clipool-workers`, `llm-workers`)
agent_config      : backend = "harness"   (cf Q4)
NATS subject      : lyra.harness.turn.request
contracts module  : roxabi_contracts.harness
heartbeat subject : lyra.harness.heartbeat
inbox prefix      : _inbox.harness.>      (auto-derived per ADR-064)
nkey secret       : lyra-nkey-harness
container         : lyra-harness          (Quadlet)
```

Aligned with `#1044` convention (now project-wide policy).

---

## 9. Runtime choice

| Runtime | Stars (verified `gh api`) | Language | State model | Verdict |
|---|---|---|---|---|
| **LangGraph** | ~6k | Python | `checkpointer` (pluggable) — `MemorySaver` = none | **Best fit.** Caller-driven context. Mature, pluggable, Python. |
| Goose | ~6k | Rust + MCP | Owns conversation, `override_conversation` escape hatch | Fights us. Use only if MCP plugins are critical short-term. |
| Hermes | ~150k* | Python | Owns conversation by default | Worth deeper look. Python advantage. |
| Pi | ~49k* | TypeScript | Lightweight, BYO-state | Polyglot pain; rules out for now. |

\* Star counts verified real via `gh api repos/...`.

**Tentative recommendation:** LangGraph with `MemorySaver`. Caller (hub) assembles context, harness invokes graph, no per-call state retained. Swappable later.
**Alternative:** custom thin loop (~200 lines of Python). Trades ecosystem (MCP plugins, prebuilt agents) for control. Reasonable if we want zero external lock-in.

---

## 10. Architecture map — crossref to open epics

```
#445  Distributed Lyra (parent)
  ├── NEW EPIC ◄── (this — harness)
  │     ├── HARD DEP: #493 (ToolHandler protocol — Q1)
  │     ├── HARD DEP: #1201 (stream() async-gen — must land first/together)
  │     └── SOFT DEP: #1198 (#665 backend-enum cleanup tail) — schedule with Q4
  │
  ├── #1044  Worker fleet (code jobs)   ←── peer wire-frame, shared JOBS bus
  │     └── #1203 JetStream JOBS bus    ←── substrate the harness can also use (Q3)
  │
  ├── #640   M4 hub statelessness        ←── blocked by NEW EPIC + #1044
  ├── #667   OTel observability (1/5)    ←── new harness must emit spans + lyra.event.*
  └── #61    Phase 3 SLMs                ←── downstream consumer (out of scope)
```

---

## 11. Reference file paths (resume cheat sheet)

### Existing canonical patterns to copy

| Concern | Reference file | Purpose |
|---|---|---|
| Hub-side `LlmProvider` over NATS | `src/lyra/nats/nats_llm_client.py` | template for `NatsHarnessClient` |
| Hub-side `LlmProvider` over NATS (CLI) | `src/lyra/llm/drivers/cli_nats.py` | template for `CliHarnessDriver` if needed |
| Worker-side NATS adapter | `src/lyra/adapters/clipool/clipool_worker.py` | template for `HarnessWorker` |
| Standalone bootstrap | `src/lyra/bootstrap/standalone/clipool_standalone.py` | template for `harness_standalone.py` |
| LLM overlay (factory) | `src/lyra/bootstrap/factory/llm_overlay.py` | template for `harness_overlay.py` |
| Worker registry | `src/lyra/nats/worker_registry.py` | reuse for harness fleet |
| Quadlet container | `deploy/quadlet/lyra-clipool.container` | template for `lyra-harness.container` |
| Circuit breaker | `packages/roxabi-nats/src/roxabi_nats/circuit_breaker.py` | reuse if Q5 ever pivots to CB quarantine |
| `LlmProvider` protocol | `src/lyra/core/ports/llm.py` | contract harness must satisfy outbound |
| `LlmEvent` types | `src/lyra/core/messaging/events.py` | `TextLlmEvent` / `ToolUseLlmEvent` / `ResultLlmEvent` |
| Stream processor | `src/lyra/core/processors/stream_processor.py` | LlmEvent → RenderEvent on hub side |
| `LlmRequest` / `LlmChunkEvent` / `LlmResponse` | `packages/roxabi-contracts/src/roxabi_contracts/llm/models.py` | canonical envelopes |
| Agent config | `src/lyra/core/agent/agent_config.py` | where `"harness"` gets added (Q4) |
| TurnStore | `src/lyra/infrastructure/stores/turn_store.py` | audit, not LLM context |
| CliPool resume state | `src/lyra/core/cli/cli_pool.py` | `_resume_session_ids` — to be replaced |
| Frame example | `artifacts/frames/1104-canonical-lyra-llm-wire-frame.mdx` | structure for harness frame |
| Object-store analysis | `artifacts/analyses/1061-nats-object-store-analysis.mdx` | substrate for by-ref transport |
| Render v2 modelling | `artifacts/analyses/1096-render-event-v2-agui-modeling-analysis.mdx` | confirms harness emits v2 only |

---

## 12. NATS subjects

### Already taken (avoid collision)

```
lyra.llm.generate.request       — llmCLI worker (NatsLlmClient)
lyra.llm.heartbeat              — llm-worker heartbeat
lyra.clipool.cmd                — clipool worker (CliPoolNatsWorker)
lyra.clipool.heartbeat          — clipool heartbeat
lyra.jobs.<domain>.<verb>       — worker fleet jobs (#1044)
lyra.results.<job_id>           — job replies
lyra.progress.<job_id>          — job progress
lyra.jobs.dlq.<domain>          — job DLQ
lyra.event.*  /  lyra.metric.*  — observability bus (#1181-#1183)
```

### Proposed harness subjects

```
lyra.harness.turn.request       — turn submission (queue: harness-workers)
lyra.harness.heartbeat          — worker heartbeat
lyra.harness.dlq                — turn DLQ (if Q5 ever pivots)
```

---

## 13. Footguns / context worth knowing

1. **Default branch is `staging`, not `main`.** Branch off `origin/staging` for any harness work.
2. **`artifacts/` is shared, tracked in git.** Don't gitignore or `rm` files here.
3. **No env-var feature gates** for the new harness — `NATS_URL` presence is enough.
4. **Quadlet is the deploy unit**, not supervisor. M₁ (prod) uses Quadlet; M₂ (dev) is manual.
5. **Error sanitization** — never let raw `str(exc)` ride on the bus. Use the post-`#1212`/`#1215` sanitization helpers.
6. **`stream()` must be an async generator** post-`#1201`. Harness `LlmProvider` impl must conform.
7. **`LlmChunkEvent.delta` is the field**, not `text`. Pre-`#1119` mismatch is fixed in canonical `NatsLlmClient`.
8. **Tool-use chunks from llmCLI are NOT emitted** by design — text-only inference there. Harness must execute tools itself, not delegate tool-use back to llmCLI.
9. **Render-events are v2-only** now. No v1 fallback. `StreamProcessor` → `ToolCallStart/Args/End/ResultRenderEvent` + `TextStart/Delta/End/ChunkRenderEvent`.
10. **`LlmChunkEvent` structurally cannot carry tool events** today (fields: `request_id, delta, done, is_error, error, duration_ms, worker_error`). Harness needs separate event taxonomy — `LlmEvent` family already exists for this.
11. **`CliChunkEvent` does carry tool events** (`event_type, text, tool_name, tool_id, tool_input, done`) but it's clipool-specific. Harness wire stays at `LlmEvent` level.
12. **`TurnStore` is audit, NOT LLM context replay.** Nothing in Lyra today rebuilds `messages[]` from TurnStore. claude-cli's `--resume` is the only mechanism keeping conversation coherent. **The harness epic is the one that introduces Lyra-owned context building.**

---

## 14. Resume checklist

1. [ ] Re-read this doc end-to-end (~7 min)
2. [ ] Quick refresh: `gh issue list --state all --search "updated:>=$(date -d '3 days ago' +%Y-%m-%d)" --limit 40` — anything new in §4 that shifts the questions?
3. [ ] Quick refresh: `git log --since="3 days ago" origin/staging --oneline | head -30`
4. [ ] **Confirm 3-concerns ownership split (§3.2)** — this is the architectural backbone
5. [ ] Re-confirm Q4 = A and Q5 = B (captured from prior conversation — worth a 30-second sanity check)
6. [ ] Answer Q1 (tool execution layer) — DP-A
7. [ ] Answer Q2 (tool-execution locality) — DP-A
8. [ ] Confirm Q3 = B (hybrid inline/by-ref) OR pivot to C (always by-ref) given `#1203` JetStream substrate
9. [ ] Confirm or amend §8 naming proposal
10. [ ] Pick runtime: LangGraph w/ MemorySaver vs custom thin loop (§9)
11. [ ] Decide sub-harness spawn = v1 or follow-up
12. [ ] Hand decisions to Claude → draft new epic body + child issue list
13. [ ] Create epic on GitHub
14. [ ] Block `#640` on the new epic + `#1044`
15. [ ] Update `#445` parent body to point at the new epic in its lane

### Decision recording — fill in when answered

```
Ownership split (3-concerns)        : confirm         : CONFIRMED 2026-05-28
Q1 (tool execution layer)           : A / B / C       : C (hybrid: shared + harness-local)
Q2 (tool-execution locality)        : A / B / C       : Hybrid: in-harness + Lyra tools via NATS (not through hub)
Q3 (wire/history transport)         : B / C           : Hybrid: text via JetStream (turns), objects via blobstore
Q4 (backend taxonomy)               : A (confirm)     : A (collapse to claude-cli | harness) — CONFIRMED 2026-05-28
Q5 (failure semantics)              : B (confirm)     : B (tool-error-soft, loop continues) — CONFIRMED 2026-05-28
Runtime                             : LangGraph / custom / Hermes : B (custom thin loop)
Sub-harness spawn                   : v1 / follow-up  : follow-up
Naming (§8)                         : OK / amend      : OK
```

### Child issues to open after epic lands

- Add `"harness"` to `_VALID_BACKENDS` (paired with `#1198`)
- `HarnessRequest` / `HarnessResponse` contracts in `roxabi-contracts`
- Runtime spike (LangGraph PoC if §9 lands there)
- `harness_standalone.py` bootstrap + Quadlet container
- Hub-side context-builder module (3-concerns #2 home)
- Hub-side write-back path on `HarnessResponse.updated_history_delta`
- Tool execution layer wiring (`#493` co-design — Q1)
- Observability: `lyra.event.harness.*` emit + OTel spans
- Integration test: stateless harness round-trip with tool execution
- ADR: harness role in distributed Lyra (`docs/architecture/adr/`) — domain page per project ADR pattern

---

## 15. Provenance

- Resume-point doc (now superseded): `artifacts/new-harness-epic-context.md` — written 2026-05-17
- Brainstorm doc (now superseded): `artifacts/analyses/harness-context-ownership.md` — written 2026-05-17
- Prior session transcripts:
  - `/home/mickael/.claude/projects/-home-mickael-projects/b63716ae-1652-4b6d-b8de-ac7e5c53a95e.jsonl` (harness-context-ownership brainstorm)
  - `/home/mickael/.claude/projects/-home-mickael-projects/d985d810-fbd3-4f96-b8b2-5ee829a8ab86.jsonl` (resume-point reconstruction)
- Last-confirmed state on staging is the head commit of `origin/staging` at write time — re-check before resuming.
