# Harness context ownership — brainstorm

**Date:** 2026-05-17
**Status:** brainstorm — not yet a frame, no epic number assigned
**Context:** consolidating prior epics #987 (tactical Anthropic deprecation via llmCLI) and #633 (strategic harness). Both closed; replacement epic to be drafted with long-term scalable framing.
**Prior conversation:** see `/home/mickael/.claude/projects/-home-mickael-projects/b63716ae-1652-4b6d-b8de-ac7e5c53a95e.jsonl`

---

## TL;DR

The question "should conversation history live in **hub** or in the **harness**?" conflates three different concerns that can — and should — be split:

| # | Concern | Recommended owner |
|---|---|---|
| 1 | **Canonical store** (durable history) | Shared persistent layer (Redis / JetStream KV, target per #640) |
| 2 | **Context builder** (assemble `messages[]` per turn) | **Hub** |
| 3 | **Turn-local working memory** (tool-call intermediates) | **Harness** |
| — | Write-back (new turn → canonical store) | Hub (single writer) |
| — | Wire transport | inline ≤256 KB else JetStream object-store ref (#1061 dep) |

Net: harness becomes **pure compute** with no `pool_id` affinity. NATS queue-group scaling falls out for free. Sub-harness spawn (Q2) is trivial. Swapping LangGraph → custom → Hermes touches only the harness.

---

## Background — current state (2026-05-17)

### What exists today

| Component | Role | Where it lives |
|---|---|---|
| `clipool_worker.py` | Streams claude-cli NDJSON → `CliChunkEvent` on NATS | `lyra/src/lyra/adapters/clipool/` |
| `CliPool._resume_session_ids` | In-memory `pool_id → cli_session_id` | hub-side, dies on restart |
| `TurnStore` (SQLite) | Audit trail of every turn (NOT replay material) | hub-side, `~/.roxabi/lyra/turn_store.db` |
| `MemoryManager` | Semantic / working / episodic memory | hub-side |
| `MessageIndex` | Lookup by `message_id` / `reply_message_id` | hub-side |
| Provider history (Anthropic `--resume`) | The actual LLM context for claude-cli | **outside Lyra** — opaque, lives in claude-cli's own state |
| llmCLI's `LlmNatsAdapter` | Canonical NATS worker for LLM generation | `llmCLI/src/llmcli/nats/llm_adapter.py` |
| `NatsLlmClient` | Hub-side canonical client (replacement for legacy `NatsLlmDriver`) | `lyra/src/lyra/nats/nats_llm_client.py` |

### Key facts established in prior investigation

1. **Wire-format mismatch (today, latent):** lyra's legacy `NatsLlmDriver._stream_gen` reads `chunk.get("text", "")`, but llmCLI sends `delta`. Silently breaks streaming. Superseded by #1119 (hub bootstrap migration to `NatsLlmClient`, which reads `chunk.delta` correctly).
2. **`LlmChunkEvent` structurally cannot carry tool events.** Fields: `request_id, delta, done, is_error, error, duration_ms, worker_error`. No `event_type`, no `tool_*`. So llmCLI cannot surface tool calls until contract evolves.
3. **`CliChunkEvent` does carry tool events** — `event_type, text, tool_name, tool_id, tool_input, done` — but it's clipool-specific, not a general harness contract.
4. **`_VALID_BACKENDS = {"claude-cli", "ollama", "litellm"}`** — `"nats"` (and any future `"harness"`) not yet in list. Follow-up.
5. **#1104 (canonical wire) is the unblock for the harness path** — coordinated merge with llmCLI #12 expected.
6. **TurnStore is audit, NOT LLM context replay.** This is critical — today nothing in Lyra rebuilds `messages[]` from TurnStore. claude-cli's `--resume` is the only mechanism keeping the conversation coherent.

### User priorities (verbatim)

- "Our priority is to have a global long term architecture for that and scalable"
- "We will have internal tool and tool used through NATS. Protocol will be likely different because we may use a pre existing tool for harness that might have plugins" (Q1)
- "Also we can have harness call the harness to create independent sub process and interact with it" (Q2 — sub-harness spawn)
- Q4 = `a`, Q5 = `b` (per prior intent clarification)
- Naming: `lyra-harness` (kebab-case, matches `lyra-clipool`, `lyra-telegram`, `lyra-discord`)
- Runtime candidates: Pi (TS), Goose (Rust+MCP), Hermes (Python), LangGraph (Python). All star counts verified real via `gh api`.

---

## Reframing — three concerns, not two locations

The hub-vs-harness debate confuses three distinct things. Treating them separately makes the answer obvious.

### Concern #1 — Canonical store (durable history)

The system of record for "every turn ever spoken in conversation `pool_id`". Survives all process restarts. Read by audit, reply-routing, memory eviction policy, multi-channel coherence.

**Today:** `TurnStore` (SQLite, hub-side).
**Target (#640):** Redis or JetStream KV.

### Concern #2 — Context builder

The function that, **per turn**, produces the `messages[]` array (plus system prompt and tool list) to ship to the LLM. Pulls from canonical store, applies policy (pruning, summarisation), injects memory (semantic/working/episodic), prepends system prompt, attaches tool descriptions.

**Today:** delegated to claude-cli's `--resume` (provider-owned context). Lyra contributes a system prompt and the new user message; everything else is opaque.

**Tomorrow:** must move into Lyra because:
- LiteLLM / local models have no provider-side session API
- llmCLI / harness path needs to know what context to send
- Memory injection needs to happen *somewhere visible*

### Concern #3 — Turn-local working memory

State that exists only during one turn of the agentic loop: parsed tool calls, intermediate results, partial assistant message under construction. Discarded after the turn completes.

**Today:** lives inside claude-cli process.
**Tomorrow:** lives inside whatever runs the agentic loop (the harness).

### Why this split matters

Every variant of "hub-owns-history" vs "harness-owns-history" is really an argument about **concern #2** (context builder). #1 has a clear long-term home (shared store, per #640's trajectory). #3 has a clear home (wherever the loop runs, by definition).

So the real question reduces to: **who builds `messages[]` per turn?**

---

## Detailed options

### Option A — Hub owns canonical store + context builder

Hub queries `TurnStore` (or its successor), assembles `messages[]`, ships to harness each turn. Harness is stateless on `pool_id`.

```
[user msg] → hub → read TurnStore → build messages[] →
             ship LlmRequest(history=messages[], tools=[…], system=…) →
             harness (stateless worker) → run loop → return updated_history →
             hub writes new turn to TurnStore → reply to user
```

#### Pros

- **Single source of truth.** No two databases to sync.
- **Harness fully stateless** → NATS queue group scales horizontally; any worker can serve any `pool_id`.
- **Sub-harness spawn (Q2) trivial.** Parent harness asks hub for a sliced history (e.g. just the sub-task scope), hub returns it. Child harness receives it the same way the top-level harness does. No state coordination.
- **Crash recovery free.** Harness dies mid-turn → hub retries with same input. No data lost (the worst case is one duplicated LLM call).
- **Memory injection in place.** `MemoryManager` is already hub-side; injection happens where the data lives.
- **Multi-channel coherence works today.** Same `pool_id` across Telegram + Discord merges naturally because hub is the routing layer.
- **Auditability.** The exact `messages[]` shipped to the LLM is observable on the wire (or logged from the hub).
- **#640 hub-statelessness compatible.** The hub stays a thin router; its "state" is the shared store, not its own memory.
- **Provider portability.** Anthropic, LiteLLM, llama.cpp, vLLM — all become the same interface from hub's perspective.

#### Cons

- **Per-turn wire payload grows** with conversation length. Mitigated by two-tier transport (inline ≤256 KB else `history_ref` to JS object store, #1061).
- **Hub does serialisation work per turn.** Marginal cost — it already queries TurnStore for the audit row.
- **If JS object-store tier never lands**, very long conversations cap at NATS max-msg size. Acceptable risk for now.
- **Memory pruning logic lives in hub**, far from the loop that "feels" the context window pressure. Aesthetic concern only; pruning is a policy decision, not a runtime one.

### Option B — Harness owns canonical store + context builder

Harness keeps history in its own per-process store (SQLite, Redis, in-memory dict). Hub sends only `(pool_id, new_user_msg)`. Harness assembles `messages[]` internally.

```
[user msg] → hub → ship (pool_id, new_msg) →
             harness reads its own store → build messages[] → run loop →
             write back to its own store → return assistant_msg → hub
```

#### Pros

- **Tiny wire envelope per turn.** Just the new message.
- **Lower-latency turn start.** No history serialisation hop.
- **Loop + history co-located.** Matches what LangGraph's `checkpointer`, Goose, Hermes all do natively. Less framework friction.
- **Harness can manage its own context window optimally** (compaction, summarisation) in the same process that sees the actual token-pressure signals.

#### Cons

- **Sticky routing required.** Every turn for `pool_id` must hit the same harness worker. Breaks NATS queue groups. Either route via consistent hashing on `pool_id`, or back the harness with a shared external store — and at that point you're at Option C anyway.
- **Double source of truth.** TurnStore in hub (audit) AND harness store (LLM context). They will drift.
- **Cross-channel coherence breaks.** Telegram + Discord under same `pool_id` requires both harnesses to share the store, again pushing toward Option C.
- **Sub-harness fork is expensive.** Child needs a snapshot of parent's history. Parent must either ship full history through the wire (defeating the wire-size advantage) or share access to its store (security/concurrency complexity).
- **Crash recovery is hard.** If harness loses session state (process death, persistence failure), hub has to reseed from TurnStore — same code as Option A, but only triggered on the bad path (untested, race-prone).
- **Memory inversion.** Lyra's `MemoryManager` is hub-side. Harness would call back into hub to read memories — wire complexity inverts and turns chatty.
- **Harness becomes the stateful component.** #640 just relocates the statefulness problem.
- **Multi-model A/B testing impossible** without duplicating state.

### Option C — Shared persistent store, neither owns it

Both hub and harness are clients of an external store (Redis / JetStream KV). Hub writes new turns, harness reads history when needed.

```
                       ┌────────── shared store ──────────┐
                       │  (Redis or JS-KV)                │
                       └────▲─────────────────────▲────────┘
                            │ write               │ read
[user msg] → hub ───────────┘                     │
                       ship (pool_id, new_msg) →  │
                                       harness ───┘
                                       → read history → build messages[] →
                                       run loop → return assistant_msg → hub
```

#### Pros

- **Hub stateless ✓ AND harness stateless ✓.** Both are clients.
- **Crash recovery trivial.** State lives outside both compute layers.
- **Natural fit with #640.** Hub already sheds Pool + sessions to Redis/JS-KV.
- **Sub-harness reads by reference, no marshalling.**
- **Auditability via store-level event log** (Redis streams, JS-KV history).

#### Cons

- **Adds a hard dependency on the store.** But we're going there anyway per #640.
- **Read consistency questions.** Two harnesses on the same `pool_id` concurrently — needs cross-process locking or single-writer discipline.
- **Latency penalty.** Every turn pays a store roundtrip in both layers.
- **Memory injection still has to happen somewhere.** If hub does it post-read, you're effectively at the Option A+C hybrid below. If harness does it, you've inverted the memory call direction again.

---

## Recommended synthesis — split the three concerns

| Concern | Owner | Rationale |
|---|---|---|
| Canonical store (#1) | **Shared persistent layer** (Redis / JS-KV per #640) | M4 trajectory already there; neither compute layer is stateful |
| Context builder (#2) | **Hub** | Only component with full Lyra context: memory, agent config, system prompt, multi-channel merging, auth, tool permissions per agent |
| Turn-local working memory (#3) | **Harness** | Naturally ephemeral; lives in the loop that uses it |
| Write-back (new turn → canonical store) | **Hub** | Single writer → no concurrency mess; matches today's TurnStore pattern |
| Wire transport | inline ≤256 KB else `history_ref` | Avoids NATS max-msg blowups; reuses #1061 object-store work |

### Why this beats both pure options

- Combines hub-owned policy wins (memory, system prompt, multi-channel) with shared-store wins (M4-ready, crash recovery, statelessness).
- Harness becomes **pure compute** — no `pool_id` affinity, no per-conversation state, NATS queue-group scales free.
- Sub-harness spawn (Q2) = `child_loop(messages_slice)`. Parent doesn't coordinate state, just passes a slice.
- Swapping the agent runtime (LangGraph → custom → Hermes → Pi) touches **only the harness**. Hub and store untouched.
- Goose-style runtimes that want to own #1+#2 collide with hub's existing ownership — Goose has `override_conversation` precisely for this, but we'd fight it every turn. LangGraph's stateless-per-call mode is the cleaner fit; runtime-agnostic the easiest.

### Data-flow diagram (target state)

```
┌──────────────────── canonical store ────────────────────┐
│   Redis / JetStream KV  (target per #640)               │
│   • full conversation history, keyed by pool_id          │
│   • semantic memories, agent prefs, pairings             │
│   • single source of truth                              │
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

### Wire shape (concrete)

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

## Implications for existing systems

### `TurnStore`
- Today: audit only. Remains audit (or merges with canonical store, TBD).
- Tomorrow: either replaced by Redis/JS-KV, or relegated to long-term archival behind the canonical store.

### `MemoryManager`
- Stays hub-side. Hub queries it during context-builder step.
- No call inversion from harness — clean.

### `MessageIndex` (reply-to routing)
- Stays hub-side. Independent of harness — hub still owns user-facing message IDs.

### `CliPool` / `clipool_worker`
- Becomes one of N harness implementations (`backend: claude-cli` → `harness=clipool`).
- Or deprecated in favour of generic harness + claude-cli as a backend tool.
- Decision: defer; #1104 + #1119 land first, then assess.

### `_VALID_BACKENDS`
- Add `"harness"` (or `"nats-harness"`) to `agent_config.py`.
- Follow-up issue.

### `_shared_streaming_emitter.py` / `StreamingSession`
- Tool event flow already handled via `ToolSummaryRenderEvent`. Stays.
- v2 typed splits (#1102) compatible — harness emits same `RenderEvent` types.

### Wire contracts (`roxabi-contracts`)
- New `HarnessRequest` / `HarnessResponse` envelopes.
- Existing `LlmRequest` / `LlmChunkEvent` / `LlmResponse` stay for raw-LLM path (llmCLI direct).
- Harness layer sits *above* the LLM layer — it consumes raw LLM responses, runs the loop, emits aggregated harness responses.

### #640 (hub statelessness M4)
- Compatible. Hub becomes a router + context-builder + store-client. Its "state" is the shared store.
- The context-builder function itself is stateless on the hub side — pure function of `(pool_id, new_msg, store_client) → messages[]`.

---

## Runtime choice (still open)

| Runtime | Stars (verified `gh api`) | Language | State model | Verdict |
|---|---|---|---|---|
| **LangGraph** | ~6k | Python | `checkpointer` (pluggable) — can be `MemorySaver` (none), or external | **Best fit.** Use `MemorySaver` (no persistence) → caller-driven context. Mature, pluggable, Python. |
| Goose | ~6k | Rust + MCP | Owns conversation, has `override_conversation` escape hatch | Fights us. Use only if MCP plugins are critical short-term. |
| Hermes | ~150k* | Python | Owns conversation by default | Worth deeper look. Python advantage. |
| Pi | ~49k* | TypeScript | Lightweight, BYO-state | Polyglot pain; rules out for now. |

\* Star counts verified real via `gh api repos/...`. Hermes 150k surprising but confirmed.

**Tentative recommendation:** LangGraph with `MemorySaver`. Caller (hub) assembles context, harness invokes graph, no per-call state retained. Swappable later if Hermes' integration story turns out cleaner.

**Alternative:** custom thin loop. ~200 lines of Python. Trades off ecosystem (MCP plugins, prebuilt agents) for control. Reasonable if we want zero external lock-in.

---

## Open questions (carry-overs)

| Q | Status | Notes |
|---|---|---|
| Q3 | inline vs JetStream-by-ref threshold | Recommend two-tier: inline ≤256 KB, else `history_ref` via JS object store (#1061). User wanted clarification on the threshold question itself. |
| Runtime | LangGraph vs custom | Tentative: LangGraph. User has not confirmed. |
| Canonical store | Redis vs JS-KV vs leave-to-#640 | Recommend leave-to-#640; harness epic stays silent. |
| Naming | `lyra-harness` (kebab, matches other adapters) | Confirmed. |
| Multi-model A/B | Out of scope for v1? | Architecture supports it for free; spec can stay silent. |

---

## What the new consolidated epic spec must say

### Must

- Replace #987 (tactical Anthropic deprecation) + #633 (strategic harness) with a single long-term architecture epic
- Harness MUST be stateless on `pool_id`
- `HarnessRequest.history` carries assembled `messages[]` (inline or `history_ref`)
- Hub owns context-builder (memory + system prompt + tool list assembly)
- Hub is single writer to canonical store
- Support sub-harness spawn (Q2) via history-slice pattern
- Wire migration aligned with #1104 (canonical `lyra.llm` subjects) and #1119 (hub → `NatsLlmClient`)

### Defers

- Canonical-store backend choice (Redis vs JS-KV) → #640
- JetStream object-store tier for large histories → #1061
- Runtime choice (LangGraph vs custom) → child issue
- MCP plugin support → child issue (Q1 second protocol)
- Deprecation of `lyra.llm.drivers.nats_driver` → follow-up

### Adjacent epics to cite

- #640 — hub statelessness M4
- #1061 — NATS object-store tier
- #1104 — canonical `lyra.llm` wire migration
- #1119 — hub bootstrap → `NatsLlmClient`
- #1102 — RenderEvent v2 typed splits
- #1192 — codec registry v2 cutover (transport encoding)

---

## References

### Files

- `lyra/src/lyra/adapters/clipool/clipool_worker.py` — clipool tool event forwarding (lines 222-261)
- `lyra/src/lyra/adapters/shared/_shared_streaming_emitter.py` — StreamingSession orchestration
- `lyra/src/lyra/llm/drivers/nats_driver.py` — legacy driver (wire mismatch, deprecated)
- `lyra/src/lyra/llm/drivers/cli_nats.py` — clipool-NATS driver (correct event parsing)
- `lyra/src/lyra/nats/nats_llm_client.py` — canonical replacement client
- `lyra/packages/roxabi-contracts/src/roxabi_contracts/llm/models.py` — `LlmRequest` / `LlmChunkEvent` / `LlmResponse`
- `lyra/src/lyra/core/cli/cli_pool.py` — `_resume_session_ids` in-memory state (to be replaced)
- `lyra/src/lyra/core/agent/agent_config.py` — `_VALID_BACKENDS` (needs `"harness"`)
- `lyra/src/lyra/infrastructure/stores/turn_store.py` — audit trail (not LLM context)
- `llmCLI/src/llmcli/nats/llm_adapter.py` — canonical worker
- `llmCLI/src/llmcli/nats/_generation.py` — `GenerationMixin` chunk emission
- `llmCLI/src/llmcli/cli_nats.py` — `nats serve llm` subcommand

### Existing artifacts

- `lyra/artifacts/frames/1104-canonical-lyra-llm-wire-frame.mdx` — canonical wire migration frame
- `lyra/artifacts/analyses/1061-nats-object-store-analysis.mdx` — object-store tier analysis
- `lyra/artifacts/analyses/1096-render-event-v2-agui-modeling-analysis.mdx` — RenderEvent v2 modelling

### Prior conversation transcript

`/home/mickael/.claude/projects/-home-mickael-projects/b63716ae-1652-4b6d-b8de-ac7e5c53a95e.jsonl`

---

## Pick-up checklist (next session)

1. Confirm or push back on the three-concerns synthesis above
2. Confirm runtime: LangGraph (`MemorySaver` mode) vs custom loop
3. Resolve Q3 inline-vs-by-ref threshold (256 KB default, configurable)
4. Decide if canonical-store choice stays with #640 or gets pulled into this epic
5. Decide if sub-harness spawn (Q2) is v1 or follow-up
6. Draft epic body using the "must / defers / adjacent" structure above
7. Open child issues:
   - Add `"harness"` to `_VALID_BACKENDS`
   - Wire mismatch handled by #1119 (verify, don't duplicate)
   - Runtime spike (LangGraph PoC)
   - `HarnessRequest` / `HarnessResponse` contracts in `roxabi-contracts`
