# Job Model — Concept Analysis

> **SUPERSEDED → `docs/architecture/job-model.md`** (living current-truth, graduated 2026-06-08).
> This analysis is the ratified-session source; it predates two doc refinements
> (concurrency_router single-stage + sub-job single-primitive transport, axial guardrails).
> Frozen historical record — do not edit; consult the domain page for current truth.

- **Date:** 2026-06-08
- **Scope:** Foundational — spans #1777, #1778, #1619, #1044, #475, #493, ADR-084
- **Status:** Analysis complete — concrete forks ready for ratification
- **Author:** architect agent
- **Trigger:** "Job" is fragmented into 4 disjoint notions; the system is bottom-up and headless; this artifact makes it top-down and job-centric.

---

## 1. Purpose & Gap

The codebase has no unified "job" concept. The word "job" names four disjoint things
(JobEnvelope.job_id, ADR-084's WorkEnvelope.job_id, HarnessRequest.request_id,
Pool.session_id) with no declared relationship between them.

**The gap:** without a shared root abstraction, every new subsystem (carrier #1778, rule
engine #475, observability #1622, harness #1490) independently invents its own identity
model, coupling, and state store — producing the multi-SSoT smell that broke `/clear`.

**The goal of this artifact:** derive a single definition of "job"; classify its shapes;
resolve which identifiers are first-class vs derived; define where state lives; show how
existing pieces re-map as facets; surface the concrete forks that block the #1777 ADR.

---

## 2. Definition: What Is a Job

A **Job** is the unit of authorised work the system tracks from the moment a request
enters the system to the moment a final response is delivered (or the work is abandoned).

Formally:

> **A job is a bounded, owned, observable unit of work with a lifecycle
> (pending → active → terminal) that the system can route to, resume, and account for.**

Three properties are constitutive:

| Property | Meaning |
|---|---|
| **Bounded** | Has a well-defined start event and at most one terminal outcome |
| **Owned** | Belongs to exactly one pool (routing scope) at any instant |
| **Observable** | Carries a stable identity that can be followed end-to-end across process boundaries |

Everything else — whether the job carries conversation history, spawns sub-jobs, calls
an LLM, runs subprocess code, or exists for 50 ms or 50 minutes — is shape-specific
behaviour, not a property of the root definition.

---

## 3. Job Taxonomy

Two primary shapes exist today. A third is latent in shipped code. A fourth (Shape D — remote stateful steerable) is analyzed in §13.

### Shape A — Conversational Job (stateful, long-lived)

- **Trigger:** user message arrives at an adapter
- **Executor:** Pool (in-process, hub-local)
- **Identity anchor:** `Pool.session_id` (uuid4, in-memory, `pool.py:105`)
- **Lifetime:** minutes to days; survives turns; cleared by `/clear`
- **History:** accumulates in `conversation_turns` (turns.db) and SDK history buffer
- **Resume semantics:** join-or-create (path-1/2/3 in `path_validation.py`)
- **Nesting:** flat — no sub-jobs today
- **State store:** `pool_sessions` row in turns.db (canonical); `KvLastSessionStore` / `ThreadStore` are stale mirrors (D1 in design-recap)
- **OTel mapping:** one session = one long-running span; each turn = child span (not yet wired — #1619 open)

### Shape B — Discrete Code Job (stateless, short-lived)

- **Trigger:** NATS publish to `factory.jobs.<domain>.<verb>` (or legacy `lyra.jobs.*`)
- **Executor:** `lyra-code-worker` container (#1044)
- **Identity anchor:** `JobEnvelope.job_id` (uuid4, always fresh — `jobs/models.py:22`)
- **Lifetime:** seconds to minutes; no cross-call state; result on `lyra.results.<job_id>`
- **History:** none — stateless by design
- **Resume semantics:** none — fire-and-result; `reply_to` field in envelope is the result inbox
- **Nesting:** composite depth ≤ 3 (`composite_depth` field, `jobs/models.py:29`, already shipped)
- **State store:** JetStream JOBS stream (#1203, not yet shipped)
- **OTel mapping:** one JobEnvelope = one span; `parent_job_id` = nesting edge (already shipped in model)

### Shape C — Agent-Turn Job (latent, harness epic #1490)

- **Trigger:** hub dispatches a turn request to the harness NATS worker
- **Executor:** `lyra-harness` stateless NATS worker (#1490)
- **Identity anchor:** `HarnessRequest.request_id` (current wire shape per #1490 spec)
- **Lifetime:** one LLM round-trip
- **History:** passed in as `history: list` in the request payload — no store of its own
- **Resume semantics:** none — request/response; hub holds state, harness is stateless
- **Nesting:** becomes a child of the Conversational Job that triggered it
- **State store:** hub-side only; harness is pure compute
- **OTel mapping:** one harness turn = one span, child of the session span

**Taxonomy table:**

| | Shape A: Conversational | Shape B: Discrete Code | Shape C: Agent-Turn (latent) | Shape D: Remote Stateful (§13) |
|---|---|---|---|---|
| Lifetime | Long (session) | Short (seconds) | Very short (LLM hop) | Long (hours) |
| History | Accumulated | None | Passed inline | Accumulated in worker |
| Resume | Join-or-create | No | No | Join-or-create (network) |
| Nesting | Flat today | Depth ≤ 3 (shipped) | Child of Shape A | Flat initially |
| Executor | Pool (in-process) | code-worker (NATS) | harness (NATS, #1490) | remote long-running worker |
| State store | turns.db | JetStream JOBS | None (hub-held) | worker-local (TBD backend) |
| Identity today | `session_id` | `JobEnvelope.job_id` | `request_id` | — (not shipped) |
| Identity in ADR-084 | `job_id` (unshipped) | `job_id` (reparent) | `job_id` (unshipped) | `job_id` = routing key (ADR-084 extension needed) |

→ See *The two orthogonal axes* (§14) for the lifecycle-nesting dimension (session/turn) orthogonal to these executor shapes.

**Join semantics:**

- Shape A only has join semantics. Path-1/2/3 in `path_validation.py` implement
  "find or create the conversational job for this message". Shapes B and C have no
  join — they always start fresh.
- Generalisation: "join-or-create" is a Shape A invariant, not a universal job property.

---

## 4. Job Identity — The Pivot (J2)

> **Superseded/amended by §15 (2026-06-08):** J2 Option 1 ("job_id=OTel-only") is replaced by §15.1 (job_id=run, durable for the whole run); the pool_id/job_id split re-framing in §15.1 supersedes the turn-granularity framing here.

**The contradiction to resolve:**

ADR-084 frames `job_id` (WorkEnvelope) as an **OTel span identity** — "which job in
the trace tree". It is explicitly NOT a routing or lifecycle lookup key. The id taxonomy
in ADR-084 is: `cli_session_id · lyra_session_id (= pool_id) · job_id (turn/span) · parent_job_id · trace_id`.

The #1777 / #1778 carrier work needs `job_id` to be an **in-process routing handle** —
something the hub can look up a pool with, resume a session with, pass through middleware.

These are not the same requirement.

**Resolved framing (this analysis):**

There are two distinct identity roles that must NOT be collapsed:

| Role | Name | Scope | Lookup key? | Stable across turns? |
|---|---|---|---|---|
| **Routing identity** | `pool_id` (= `RoutingKey.to_pool_id()`) | Process-local, hub-managed | Yes — primary key in turns.db | Yes — permanent |
| **Work-span identity** | `job_id` (ADR-084) | Cross-process, OTel | No — trace annotation only | No — per-turn/span |

`pool_id` is already the canonical routing and lifecycle key (`hub_protocol.py:142`,
`turn_store_protocol.py:74`, `ADR-001 §4`). It is permanent for the lifetime of a pool.

`job_id` (ADR-084) is the OTel span id for one unit of work (one turn, one code job, one
harness hop). Many `job_id`s belong to one `pool_id`. `job_id` rotates per turn;
`pool_id` does not.

**The contradiction in ADR-084's id taxonomy:** the doc lists `lyra_session_id (= pool_id)`
and `job_id (turn)` as separate ids, which is correct — but the carrier epic (#1778) has
been discussed as if `job_id` could substitute for `pool_id` as the in-process routing
handle. It cannot. ADR-084 must be read as: `job_id` annotates work for observability;
`pool_id` routes work in the hub.

**Resolution for J2:**
- `pool_id` = routing/lifecycle key — unchanged, already correct
- `job_id` (ADR-084) = OTel span / work-span identity — scoped to cross-process tracing
- The in-process carrier (#1778) threads `job_id` alongside `pool_id`, not instead of it
- No new "job routing" lookup table is needed; `get_last_session(pool_id)` remains the
  correct pattern for last-session resolution

---

## 5. Job Lifecycle State Machine

### Shape A — Conversational Job

```
                  ┌──────────────────────────────────────────────┐
                  │                                              │
  message         │  new message                                 │
  arrives         ▼  (same pool)                                 │
──────────► [PENDING] ──join-or-create──► [ACTIVE] ──turn done──► [IDLE]
                                              │                   │
                                         /clear cmd          pool gc /
                                              │              inactivity
                                              ▼                   │
                                        [CLEARED] ◄───────────────┘
                                              │
                                        next message
                                              │
                                              ▼
                                        [ACTIVE] (fresh session_id)
```

**Event mapping:**

| Transition | NATS event / code |
|---|---|
| PENDING → ACTIVE (new session) | `pool.reset_session()` → publishes `start_session` → turn-writer INSERTs `pool_sessions` row |
| PENDING → ACTIVE (resume) | `pool.resume_session(session_id)` → `path_validation.py` path-1/2/3 |
| ACTIVE → IDLE | turn completes; `pool.is_idle = True` |
| IDLE → ACTIVE | next message triggers pipeline |
| ACTIVE → CLEARED | `cmd_clear` → `pool.reset_session()` |
| CLEARED → ACTIVE | next message triggers fresh `reset_session()` |

### Shape B — Discrete Code Job

```
 publish to              worker             result on
 lyra.jobs.*  ──────► [ACTIVE] ──────► [SUCCESS | ERROR]
                          │
                    DLQ on max_deliver
                          │
                          ▼
                       [DEAD]
```

**Event mapping:**

| Transition | Mechanism |
|---|---|
| → ACTIVE | JetStream deliver to code-worker queue group (#1203) |
| → SUCCESS | `JobResult(status="success")` on `reply_to` subject |
| → ERROR | `JobResult(status="error", error=WorkerError)` on `reply_to` subject |
| → DEAD | JetStream DLQ after `max_deliver` exhaustion (`lyra.jobs.dlq.<domain>`) |

### Shape C — Agent-Turn Job (latent)

```
 hub dispatches          harness            hub receives
 HarnessRequest ──────► [ACTIVE] ──────► [SUCCESS | ERROR]
```

No resume, no DLQ — synchronous request-reply. Timeout = hub-side concern.

---

## 6. Join-or-Create Decision (Generalised)

The current path-1/2/3 system in `path_validation.py` implements join-or-create for
Shape A only. Generalised as a routing decision:

```
function route_message(msg, pool_id) -> session_id:

  // path-1: explicit user intent (reply to a known message)
  if msg.reply_to_id is not None:
    session_id = message_index.resolve(pool_id, msg.reply_to_id)
    if session_id is not None:
      return session_id   // join existing session by reply
    // else: fall through (message may have been GC'd or belong to another pool)

  // path-2: adapter-scoped thread resume
  // NOTE: D1 locks this path for removal after #1777
  // The adapter mirror (KvLastSessionStore / ThreadStore) is the liar.
  // After #1777, this path is eliminated; adapters carry no session pointer.
  if msg.platform_meta.thread_session_id is not None:
    session_id = turn_store.get_session_pool_id(thread_session_id)
    if session_id is not None and owns(pool_id, session_id):
      return session_id   // join existing session by thread (pre-#1777)
    // else: stale pointer — fall through

  // path-3: last-active (only when pool is idle — avoids mid-turn hijack)
  if pool.is_idle:
    session_id = turn_store.get_last_session(pool_id)
    if session_id is not None:
      return session_id   // continue last session

  // create: no existing session found (or pool busy, or /clear just ran)
  return pool.reset_session()   // mints new session_id, publishes start_session
```

**Post-#1777 simplification:** path-2 is removed; join-or-create collapses to:

```
path-1 (explicit) → path-3 (last-active, idle-gated) → create
```

**Rule engine intersection (Q12 from design-recap):**

`#475` is scoped to trigger → action routing (bare_url → vault-add, any_message →
auto-respond). It sits at the inbound router stage (`src/lyra/inbound/router.py`,
`RouterCtx.watch_channels`). The join-or-create decision is a different concern
(session continuity, not channel behaviour). They do NOT merge. Path-priority stays
hardcoded in middleware; the rule engine evaluates channel-scoped behaviours after
the pool is resolved.

---

## 7. Job ↔ Worker Relationship

```
Job Shape       │  Executor             │  Coupling model
─────────────── │ ───────────────────── │ ──────────────────────────────
Shape A         │  Pool (in-process)    │  Tight — Pool IS the executor; job = pool lifecycle
Shape B         │  code-worker (NATS)   │  Loose — JetStream queue group; any worker instance handles
Shape C (latent)│  harness (NATS)       │  Loose — request-reply; hub is caller; harness is stateless
```

**Worker taxonomy (per #1044 locked decisions):**

| Worker | Transport | Stateful? | LLM access |
|---|---|---|---|
| Pool (Shape A) | In-process | Yes (SDK history) | Yes (via LlmProvider port) |
| code-worker (Shape B) | NATS JetStream | No | Via LlmProvider port (ctx.llm) |
| harness (Shape C, #1490) | NATS request-reply | No | Direct (is the LLM turn executor) |

The `lyra-clipool` container is Shape A's external executor (subprocess claude-CLI).
From the hub's perspective, `clipool` is not a "worker" in the Shape-B sense — it is the
backend for the Pool's LLM calls. The taxonomy in #1044 collapses `claude-cli | harness`
as backend options for Shape C; litellm / ollama become harness models, not separate
worker shapes.

**Composite jobs (Shape B → Shape B nesting):** the `composite_depth ≤ 3` invariant
(`jobs/models.py:29`) already ships. Composites are NATS-mediated even when handlers
co-locate (`ctx.jobs.request()`). This is the correct pattern — splitting is a deploy
operation, never a protocol change.

---

## 8. Job State Store

**Constraint hierarchy:**
- D2 (locked): SQLite now — KV/Redis only if multi-machine forces it
- D1 (locked): turn-writer is sole writer to turns.db
- ADR-075 / ADR-078: `TurnWriteEvent` NATS subject; turn-writer handles `start_session`, `log_turn`
- `TurnStoreProtocol.get_last_session()` (`turn_store_protocol.py:74`) = sole read-path for Shape A last-session in core

**State by shape:**

| Shape | State | Store | Writer | Reader |
|---|---|---|---|---|
| A: Conversational | session_id (current) | `pool_sessions` (turns.db) | turn-writer (ADR-075 sole writer) | `TurnStoreProtocol.get_last_session(pool_id)` |
| A: Conversational | message → session mapping (reply-to) | `message_index` KV (`factory-turns-meta`) | adapter (message event) | `hub._message_index.resolve()` — path-1 |
| A: Conversational | conversation turns | `conversation_turns` (turns.db) | turn-writer | `TurnStoreProtocol.get_turns()` |
| B: Discrete Code | job in-flight / DLQ | JetStream JOBS stream (#1203, not shipped) | JetStream (broker-managed) | consumer / DLQ subscriber |
| B: Discrete Code | job result | ephemeral reply-to inbox | code-worker (publishes JobResult) | hub / original caller |
| C: Agent-Turn | none — stateless | — | — | — |

**The stale mirrors to remove (#1777):**

| Store | Backend | Remove how |
|---|---|---|
| `KvLastSessionStore` | NATS-KV `factory-turns-meta` | Drop from resume path; keep only for message_index use (separate fact — path-1) |
| `ThreadStore.session_pointer` | `discord.db` | Keep `claim`, drop `update_session` and session-pointer field |

After #1777, `turns.db` `pool_sessions` is the sole SSoT for Shape A last-session.
`get_last_session(pool_id)` is the only query. No new port method needed — path-2 is
eliminated, not replaced.

**On Q6 (LastSessionStore port survival):**

The `LastSessionStore` port (`core/ports/last_session_store.py`) has only `get/set`. The
set path is a no-op in `TurnStoreLastSession.set_last_session` (`bootstrap/wiring/last_session_wiring.py`).
After #1777, the port has only one meaningful implementation: a thin wrapper that delegates
to `TurnStoreProtocol.get_last_session()`. The port can be collapsed — core can call
`turn_store.get_last_session()` directly (it already holds the protocol reference). Keeping
the port adds indirection with no second implementation. Recommended: **collapse the port**.

---

## 9. Re-Derivation Map

Existing pieces as facets of the job model:

| Existing piece | File anchor | Job model facet |
|---|---|---|
| `Pool.session_id` | `pool.py:105` | Shape A job identity (current; becomes `job_id` post-ADR-084 reparent) |
| `RoutingKey.to_pool_id()` | `hub_protocol.py:142` | Routing scope — permanent, not a job id |
| `pool_sessions` table | `turn_store_queries.py` `get_last_session` | Shape A job state store (SSoT, canonical) |
| `TurnStoreProtocol.get_last_session` | `turn_store_protocol.py:74` | Shape A join-or-create resolver (path-3) |
| `message_index` KV | `path_validation.py:55-102` | Shape A join by reply-to (path-1 — separate fact, NOT a session mirror) |
| `KvLastSessionStore` | `infrastructure/stores/turn_session_kv.py:118` | Stale mirror → remove (D1) |
| `ThreadStore` session-pointer | `infrastructure/stores/thread_store.py` | Stale mirror → drop session-pointer (D3) |
| `JobEnvelope` | `jobs/models.py:21` | Shape B job envelope (SHIPPED, extends ContractEnvelope not WorkEnvelope yet) |
| `composite_depth` | `jobs/models.py:29` | Shape B nesting depth limiter (SHIPPED) |
| `HarnessRequest.request_id` | #1490 wire spec | Shape C job identity (pre-ADR-084; becomes `job_id` when harness ships) |
| `WorkEnvelope.job_id` | ADR-084, #1619 | Cross-shape OTel span id (ACCEPTED, NOT SHIPPED) |
| `WorkScope` | `transport/work_scope.py` (#1375) | Routing metadata ("where") — composes into WorkEnvelope, ≠ job id |
| `InboundContext` | `inbound/context.py` | Ingress-side composite — Shape A job context at entry point |
| `PipelineContext` | `hub/middleware/middleware.py` | Hub-side mutable context — Shape A job context in flight |
| `path_validation.py` path-1/2/3 | `hub/middleware/path_validation.py:55-213` | Shape A join-or-create logic |
| `cmd_clear` | `core/commands/workspace_commands.py` | Shape A job termination (rotate session_id) |
| `rule engine` | #475 (OPEN) | Post-routing channel behaviour — evaluates against resolved Shape A job context |
| `JOBS stream + DLQ` | #1203 (OPEN) | Shape B job state store (not shipped) |

---

## 10. Decisions for Ratification

### J1 — Shape: is the taxonomy closed at 2, or is Shape C a first-class shape now?

```
── Decision: J1 — Job shape taxonomy closure ──
Context:     Shape C (agent-turn / harness) exists in #1490 spec but harness is unshipped.
             Treating it as a first-class shape now adds modeling overhead for unimplemented code.
             Not acknowledging it risks Shape B / Shape C conflation when #1490 ships.
Target:      Correct taxonomy for #1777 ADR scope; avoid premature over-engineering.

Options:
  1. Close at 2 shapes (A + B); Shape C is "a future variant of B" — harness = code-worker subtype
  2. Acknowledge 3 shapes (A + B + C); Shape C is latent, no impl yet   ← recommended
  3. Treat Shape C as Shape A's sub-execution (pool dispatches to harness as a backend detail)

Recommended: Option 2 — Shape C is genuinely distinct (no history, hub-caller, LLM executor) but
             latent; acknowledge now, implement when #1490 ships. No modelling needed in #1777.
```

### J2 — Identity: job_id as routing/lifecycle key vs OTel span identity

```
── Decision: J2 — job_id role ──
Context:     ADR-084 defines job_id as OTel span id, NOT routing key. The carrier epic (#1778)
             has been discussed as if job_id could substitute for pool_id as routing handle.
Target:      Single unambiguous identity model for all three shapes and for #1777/#1778 ADRs.

Options:
  1. job_id = OTel span ONLY; pool_id remains the routing/lifecycle key (¬overlap)   ← recommended
  2. job_id = dual-purpose (OTel + routing); pool_id deprecated for Shape A
  3. Introduce a third id (e.g. "turn_id") to decouple routing from OTel

Recommended: Option 1 — pool_id is permanent and already wired everywhere; job_id rotating
             per turn is incompatible with being a durable routing key. ADR-084 is correct
             as written. The carrier (#1778) threads job_id alongside pool_id, not instead.
             Refined post use-case analysis: the conversational job identity is `session_id`,
             not `pool_id`; `pool_id` is the address of the container that holds the current
             job (see *The two orthogonal axes*, §14).
```

### J3 — Job state store: LastSessionStore port collapse vs keep

> **Superseded/amended by §15 (2026-06-08):** D1 and J3 are independently shippable (§15.2); ship D1 (path-2 removal) first, J3 (inbound dead-read removal) second; they touch different methods.

```
── Decision: J3 — LastSessionStore port ──
Context:     Port has get/set only; set is a no-op; no second implementation planned.
             After #1777 (D1), the sole reader is turn_store.get_last_session(pool_id).
Target:      Remove redundant abstraction without breaking layering (core ¬import infra rule).

Options:
  1. Collapse port — core calls turn_store.get_last_session() directly; port deleted   ← recommended
  2. Keep port — add clear_last_session(); implement in TurnStoreLastSession as no-op cascade
  3. Keep port — collapse to a one-method get-only port wrapping TurnStoreProtocol

Recommended: Option 1 — core already holds TurnStoreProtocol (a driven port in core/ports/);
             LastSessionStore is a redundant layer over it. Collapsing simplifies path-3 and
             removes dead set/clear surface. No second implementation = no reason for indirection.
```

### J4 — Carrier shape: does #1778 need a foundational "JobContext" ADR before #1777's ADR?

```
── Decision: J4 — ADR sequencing ──
Context:     #1777 ADR = SSoT consolidation (D1). #1778 = in-process carrier (blocked by #1619).
             Q2 in design-recap asks if carrier must reconcile ADR-073 (per-stage contexts).
Target:      Unblock #1777 ADR without committing to #1778 carrier shape prematurely.

Options:
  1. Write #1777 ADR standalone (SSoT only); carrier shape deferred to #1778 ADR   ← recommended
  2. Write a combined "Job Foundation" ADR covering SSoT + carrier + id model
  3. Block #1777 ADR until #1619 (WorkEnvelope) ships

Recommended: Option 1 — D4 (locked) already states #1777 ships independently. The #1777 ADR
             needs only G3+G4 resolved (Q6-Q10). This analysis resolves Q6 (collapse port),
             Q7 (remove path-2 + simplify _build_thread_path to pool-owned-only logic),
             Q8 (ThreadStore keeps claim, drops update_session + session_pointer field).
             Q9 and Q10 are product decisions below.
```

### Fork F1 — Reply-to vs /clear conflict (Q9)

```
── Decision: F1 — reply-to vs /clear semantics ──
Context:     If a user replies to a pre-clear message, path-1 would resume the cleared session
             (reply_to_id → message_index → old session_id). Is this correct or a bug?
Target:      Clear, documented product intent for /clear behaviour with explicit vs implicit resume.

Options:
  1. Explicit user intent wins — reply-to resumes even after /clear (path-1 stays above clear)
  2. /clear is absolute — clear invalidates ALL resume paths including reply-to
  3. /clear invalidates last-active only; reply-to (explicit) and thread-scope (post-D3) unaffected

Recommended: (product decision — cannot be resolved by architecture analysis alone)
             Architectural note: Option 3 is the current de-facto behaviour after D1+D3+#1777;
             path-1 is "explicit user intent" by design (design-recap §2). If Option 2 is chosen,
             message_index.resolve() must check whether the target session was cleared, requiring
             a cleared-session tombstone in turns.db — new schema + writer change. Option 3 = zero
             new schema.
```

### Fork F2 — CLI --resume failure eviction (Q10)

```
── Decision: F2 — CLI --resume failure handling ──
Context:     If CLI --resume fails (empty reply / non-zero exit), the session pointer in the
             adapter may be left dangling, causing path-2 resurrection (pre-#1777) or path-3
             to return a non-resumable session_id (post-#1777).
Target:      Clear failure semantics that don't produce zombie sessions.

Options:
  1. --resume failure → hub detects empty/error reply → publishes new start_session → fresh session
  2. --resume failure → CLI adapter emits a resume_failed event → hub evicts session pointer
  3. --resume failure treated as turn failure (user sees error); session pointer unchanged

Recommended: (product decision on UX) — architectural note: after #1777 (D1), the adapter holds
             no session pointer to evict. The issue reduces to: does hub path-3 return a stale
             cli_session_id reference? Yes if turns.db has a cli_session_id row that no longer
             maps to a live claude-cli session. This is orthogonal to #1777 and should be tracked
             as a separate issue rather than blocking the SSoT consolidation.
```

### F2 extended — working directory (`cwd`) is hub-owned session-materialization state

A planned Telegram **`/folder`** command sets the working directory per-conversation, turning `cwd` from a static default (`config.toml [defaults]`) into **per-conversation state**. This extends F2, because resume now needs the cwd.

**Grounding fact:** claude-CLI **namespaces sessions by cwd** — `--resume <session_id>` only works when relaunched in the *same directory* the session was created in. (This is the `lyra→factory` rename-bug root: changing cwd orphaned the session dirs.) Therefore the resume tuple is **`(session_id, cwd)`**, not `session_id` alone. `cwd` is **not** identity (`session_id` is, per J2) — it is a **materialization attribute**.

**Granularity — pool and session:**

| Level | Role | Survives `/clear`? |
|---|---|---|
| **Pool (container)** | `pool.cwd` = current working directory ("this conversation works in X") | ✅ (reset context, stay in the folder) |
| **Session** | cwd captured at materialization (first turn), stored; frozen at creation | — |

Both are needed: the pool carries the current cwd (for *new* sessions), but because claude-CLI locks a session to its cwd, **each session freezes its cwd at creation** — otherwise resume could not know where to relaunch if the pool's cwd changed since.

**Ownership (same partition as adapter effects):**
```
HUB     decides + stores : pool.cwd (in-memory current) + per-session cwd in turns.db
                           (turn-writer, sole writer, ADR-075 — same SSoT as session)
CLIPOOL consumes         : hub → clipool request carries {cwd, session_id, --resume};
                           launches the subprocess there, holds nothing (effector)
```

**`/folder` mechanics:** `/folder <path>` is a **hub command** (like `/clear`). With **lazy session materialization** (the CLI session is created on the first turn), the sequence `/clear → /folder → message` works: `/clear` mints a session_id (in-memory, not materialized), `/folder` sets `pool.cwd`, the first message materializes the session in that cwd and stores it. If `/folder` is issued **mid-session** (session already materialized in the old cwd), it **forces a session boundary** (≈ implicit `/clear`, new session in the new cwd) — claude-CLI cannot move a session across cwd. Communicate this to the user.

**The F2 extension:** resume = `(session_id, cwd_stored)`. If `cwd_stored` is missing / the dir was deleted → resume fails → fresh fallback (in `pool.cwd` or the default). This **resolves Q5** (the `cli_session_id`↔cwd coupling) by making cwd **explicit stored data** rather than implicit process state → the rename bug becomes a **one-time migration** (update stored cwds) instead of silent breakage.

**⚠️ Security constraint (track with the feature):** `/folder <path>` = arbitrary cwd = the agent operates on any directory the container can access. Must be **bounded** — allowlist of mounted roots; `dev-core:security-auditor` at the feature spec. Does not block the design, but is an explicit constraint.

**Sequencing:**
- The **cwd-per-session schema** can be anticipated when #1777 touches the session record (reserve a `cwd` column, like `job_id` nullable in #1778).
- The **`/folder` command + resume-with-cwd + F2 fallback + security bound** = the F2-extension issue (separate, does **not** block #1777).

---

## 11. Sequencing Impact

**Does the job model need a foundational ADR before #1777's ADR?**

No — but this analysis (not an ADR) is the required prerequisite.

The #1777 ADR is scoped to Shape A SSoT consolidation. It does not need to commit to:
- Shape C (harness is unshipped; #1778 is blocked by #1619)
- The full job taxonomy (Shapes B and C have no coupling to #1777)
- WorkEnvelope reparenting (ADR-084 is already accepted; #1619 implements it; not #1777's scope)

**Shape D decisions (D-1…D-6) are in §13. They do not block #1777 but do affect J2 and ADR-084.**

**What #1777 ADR DOES need (all resolved by this analysis):**

| Question | Resolution |
|---|---|
| Q6: LastSessionStore port survive or collapse? | Collapse — Option 1 (J3 above) |
| Q7: What happens to `_build_thread_path` after path-2 removal? | Simplify to pool-owned-only routing; remove KvLastSessionStore resume branch |
| Q8: ThreadStore reduced surface — what stays? | Keep `claim`, drop `update_session` + session-pointer field |
| Q9: reply-to vs /clear conflict | F1 — product decision; recommend Option 3 (path-1 survives /clear) = zero schema change |
| Q10: --resume failure eviction | F2 — architectural recommendation: track separately; does not block #1777 |

**Updated sequencing:**

```
NOW (unblocked):
  Ratify J1, J2, J3, J4, F1, F2 (or defer F1/F2 as product decisions)
  → /frame #1777 → #1777 ADR (SSoT consolidation, path-2 removal, port collapse)
  → Impl #1777

PHASE 1 (after #1619 WorkEnvelope ships):
  → #1778 carrier ADR (reconcile ADR-073, thread job_id alongside pool_id)
  → Shape A gets job_id annotation in InboundContext / PipelineContext

PHASE 2 (after #1778):
  → #475 rule engine evaluates against resolved pool + carrier context
  → Shape B JOBS stream (#1203) → code-worker (#1046, #1047)
  → Shape C harness (#1490) when unblocked
```

---

## 12. Open Questions / Unverified

**Unverified (not derivable from read artifacts — requires code read or product input):**

| # | Item | Why unverified |
|---|---|---|
| U1 | Exact `_build_thread_path` logic in `inbound/session_builder.py` | File not read in this analysis; code anchor noted in design-recap but content unverified |
| U2 | Whether `ThreadStore.claim` remains in scope post-D3 or Discord thread pool-ownership makes `claim` redundant too | Requires reading `infrastructure/stores/thread_store.py` + Discord adapter claim flow |
| U3 | Whether `message_index` KV is written by the adapter (path-1 is "separate fact" per design-recap §1 and §2) or by turn-writer | design-recap states it's a separate fact; ownership not verified in code |
| U4 | Exact failure mode of CLI `--resume` (empty reply vs exit code vs NATS timeout) | Requires reading clipool adapter + CLI session lifecycle code; F2 recommendation may need revision |
| U5 | Whether `pool.is_idle` check in path-3 (`path_validation.py:170-213`) is sufficient to prevent mid-turn session hijack under concurrent message delivery | Requires reading concurrent-delivery handling in Pool; relevant to correctness of join-or-create pseudocode in §6 |

**Open product decisions (cannot be resolved by architecture analysis):**

| # | Question | Section |
|---|---|---|
| P1 | Reply-to vs /clear: does explicit reply-to trump a /clear? | J1, F1 |
| P2 | --resume failure UX: error to user, or silent fresh session? | F2 |
| P3 | When is the forcing function for multi-machine hub (Q11)? Determines whether D2 SQLite is a permanent decision or a "for now" placeholder | design-recap Q11 |
| P4 | Does path-priority (path-1/2/3) ever become rule-engine-driven (#475 Q12)? | design-recap Q12; this analysis says no (separate concerns) — but is a product decision if configurable resume is desired |

---

## References

- `artifacts/analyses/1778-jobcontext-session-ssot-design-recap.md` — locked decisions D1-D4, open Q1-Q13
- `artifacts/analyses/workenvelope-job-id-invariant.md` — WorkEnvelope design source
- `artifacts/analyses/1490-harness-composition-analysis.mdx` — harness epic analysis
- `docs/architecture/adr/084-workenvelope-job-id-invariant.mdx` — ADR-084 (accepted, not shipped)
- `src/factory/core/pool/pool.py:105, 238-258` — session_id mint + reset_session
- `src/factory/core/hub/hub_protocol.py:136-142` — RoutingKey.to_pool_id()
- `src/factory/core/hub/middleware/path_validation.py:55-213` — path-1/2/3
- `src/factory/core/stores/turn_store_protocol.py:74` — get_last_session
- `packages/roxabi-contracts/src/roxabi_contracts/jobs/models.py:21-29` — JobEnvelope + composite_depth
- Issues: #1777, #1778, #1619, #1044, #1490, #475, #493, #1203
- Shape D analysis: §13 below

---

## 13. Shape D — the remote stateful worker job (the worker-future)

- **Date added:** 2026-06-08
- **Trigger:** Product owner's stated target: *"on va avoir des workers qu'on va lancer potentiellement à terme avec des jobs. Quand on envoie un message sur Telegram/Discord, c'est censé voir si ce message est lié à un job existant ou pas et s'il est pas lié à un job existant le lancer."* Concrete instance: launch a worker that researches for an hour; while it runs I can send it steering messages.
- **Shape:** Remote + stateful + addressable-by-follow-up-message.

---

### D-1: Is Shape D a genuinely new shape or a composition of existing shapes?

**Position: genuinely new.**

Shape D cannot be reduced to an existing shape:

| Test | Result |
|---|---|
| Is it a Pool (Shape A)? | No — Shape A is in-process, hub-local; the executor here is a remote process outside the hub |
| Is it a code-worker (Shape B)? | No — Shape B is stateless and short-lived; Shape D accumulates state and runs for hours |
| Is it a harness (Shape C)? | No — Shape C is one LLM hop; Shape D is a long-running autonomous process |
| Is it a composition (A + B)? | Partially — it inherits join-or-create semantics from A and remote execution from B; but neither composition covers the steer channel requirement |

The steer channel is the discriminator. No existing shape has: a live remote process that (1) holds its own accumulated state and (2) can receive new inbound messages mid-run routed to it specifically. This is an emergent capability, not a recombination of existing parts.

**New taxonomy row (Shape D):**

- **Trigger:** inbound message that the rule engine (#475) classifies as "launch new worker job" (no live matching job found) or "steer existing worker job" (live matching job found)
- **Executor:** remote long-running worker process (new worker type; not clipool, not code-worker, not harness)
- **Identity anchor:** `job_id` — durable, NATS-addressable routing key (see D-2)
- **Lifetime:** hours; not turn-bounded
- **History:** accumulated inside the worker process; worker owns its own state
- **Resume semantics:** join-or-create with network lookup (not FS-local; see D-3)
- **Nesting:** flat initially; the product has not specified sub-job requirements for Shape D
- **State store:** worker-local (backend unspecified; out of scope for #1777/#1778)

---

### D-2: Does Shape D re-open J2 (job_id = OTel-only vs network-addressable routing key)?

> **Superseded/amended by §15 (2026-06-08):** job_id=run (§15.1) dissolves the D-2a overload problem — no separate `worker_id`/`steer_target` field needed; job_id is durable for the whole run and steer targets it natively.

**Position: yes — J2 is re-opened for Shape D. The J2 recommendation stands for Shapes A/B/C but does not cover Shape D.**

The J2 recommendation (§10) is: `pool_id` = routing/lifecycle key; `job_id` = OTel span identity only; these must NOT be collapsed. That reasoning is sound for Shapes A/B/C:

- Shape A: `pool_id` is the routing key; `job_id` rotates per turn (a turn span, not the pool lifetime).
- Shape B: `JobEnvelope.job_id` is always fresh (fire-and-result); no routing handle needed after dispatch.
- Shape C: one-hop; no durable routing handle needed.

Shape D breaks the premise: **there is no `pool_id` analog**. Shape D workers are remote; there is no in-process Pool. The routing-key role vacated by the absent `pool_id` must be filled by something. The only candidate in the existing identity model that spans the job's lifetime is `job_id`.

**Position: for Shape D, `job_id` is BOTH the OTel annotation AND the network-addressable routing identity.** ADR-084 must be extended with a Shape D exception: *"For remote stateful workers (Shape D), `job_id` is a durable routing key in addition to its OTel role."*

This does NOT invalidate the J2 recommendation for Shapes A/B/C. It adds a shape-specific clause.

**Required ADR-084 change:** a single section extension acknowledging the Shape D exception. No rework of the existing id taxonomy needed.

---

### D-3: By what NATS subject does a steering message reach a live worker? Discovery mechanism?

> **Superseded/amended by §15 (2026-06-08):** steer subject confirmed `factory.job.<id>.steer` (§15.6); registry schema, liveness/TTL model, and hub-sole-writer pattern fully specified in §15.3.

**Proposed steer subject:** `factory.job.<job_id>.steer`

**Rationale:** `factory.progress.<job_id>` already exists (`jobs/models.py` — worker→caller direction). The steer channel is the symmetric inbound: `factory.job.<job_id>.steer` (caller→worker direction). The `<job_id>` token in the subject satisfies `WorkScope` validation constraints (uuid4.hex = 32 hex chars; within the 48-char limit; alphanumeric).

**Discovery mechanism:**

1. **Worker announces liveness:** on startup, the worker publishes its `job_id` → NATS-KV bucket `factory-active-jobs` (keyed by `job_id`, value = worker identity / steer subject). TTL = heartbeat interval (e.g., 30 s); worker refreshes while alive; expiry = worker dead.
2. **Hub lookup before dispatch:** `path_validation.py` join-or-create for Shape D calls `active_jobs_kv.get(job_id)` before deciding steer vs launch. This lookup is network-accessible (NATS-KV, not FS-local SQLite) — resolving the gap identified in the J2 re-opening.
3. **Rule engine consumes the lookup result:** #475 receives `{job_id_from_message, live: bool, steer_subject: str | None}` → emits `steer(steer_subject)` or `launch(new_job_id)`.

**Why NATS-KV for liveness, not turns.db:** Shape D workers are remote (not hub-local). turns.db is FS-local SQLite on the hub (`factory/infrastructure/` layer). A remote worker cannot write to it without introducing an infrastructure coupling across process boundaries. NATS-KV is already a shared bus artifact; both hub and remote worker have access. This is the forcing function D2 locked on (design-recap §3) re-surfaced for Shape D: KV is justified here precisely because hub-local SQLite cannot serve as a network-accessible registry.

**No change to `factory.progress.<job_id>`:** progress remains unidirectional worker→caller, best-effort. Steer is a separate subject and a separate ACL grant.

---

### D-4: Where does Shape D sit in the worker taxonomy?

**Position: Shape D is a 4th executor type, distinct from Pool / code-worker / harness.**

| Executor | Shape | Duration | State | Steer |
|---|---|---|---|---|
| Pool (in-process) | A | Session | hub-held | via new inbound turn |
| code-worker | B | Seconds | none | n/a |
| harness | C | LLM hop | none (hub-held) | n/a |
| **remote long-running worker** | **D** | **Hours** | **worker-held** | **`factory.job.<id>.steer`** |

The Shape D executor is not clipool (`lyra-clipool` is a subprocess backend for Shape A Pools, not a standalone worker). It is not a code-worker extended (code-workers are stateless by design). It is a new container/process role that the system does not yet have.

**Implementation note:** the Shape D worker type requires:
- A new NATS ACL grant for `factory.job.*.steer` (subscribe)
- A new heartbeat/liveness subject for `factory-active-jobs` KV writes
- This is a Phase 2+ item (after #1619 + #1778); no code change needed now

---

### D-5: Interplay of ToolRegistry (#493) and rule engine (#475) with steer-vs-launch decision

**Position: rule engine (#475) owns the steer-vs-launch routing decision; ToolRegistry (#493) is orthogonal and does not participate in it.**

**#475 (rule engine) — steer-vs-launch:**

The rule engine sits at `inbound/router.py` (where channel-scoped routing decisions already live). The steer-vs-launch decision is a routing rule: "given this inbound message and the current state of active jobs, which executor handles it?" This is precisely the rule engine's scope.

Concrete rule (to be expressed in #475's DSL when shipped):

```
IF message.context.job_id IS SET
   AND active_jobs_kv.get(message.context.job_id) IS LIVE
   THEN route → factory.job.<job_id>.steer
ELSE
   THEN launch → new Shape D worker with fresh job_id
```

The `job_id` context must be surfaced from the inbound message — either explicit (user command `/steer <job_id>`) or inferred from session context (the pool's current active job). This inference is a product decision not yet specified.

**#493 (ToolRegistry) — Shape D capability declaration:**

ToolRegistry declares what capabilities a worker instance supports. For Shape D workers that accept steer messages, the relevant capability is: "accepts mid-run steering". ToolRegistry is consulted at launch time to validate the worker can receive steering, NOT at steer time (by then the worker is already running and has already published its steer subject). ToolRegistry does not participate in the steer-vs-launch routing decision itself.

---

### D-6: Impact on locked decisions and open artifacts

**J2 recommendation (§10):** Extended, not replaced. The existing recommendation (pool_id = routing key for Shapes A/B/C) stands. Shape D adds a clause: for Shape D, `job_id` IS the durable routing key because no pool_id analog exists. ADR-084 requires a one-section extension.

**ADR-084 (WorkEnvelope):** Must add a `### Shape D exception` section. The core id taxonomy is unchanged; the exception is bounded to remote stateful workers. No rework needed.

**#1777 (Shape A SSoT consolidation):** Unaffected. Shape D has no overlap with turns.db, pool_sessions, path-1/2/3 for Shape A, or the KvLastSessionStore removal. #1777 ships as planned.

**#1778 (In-process JobContext carrier):** Shape D adds one constraint: the carrier must NOT treat `job_id` as a pure OTel annotation when a Shape D worker is in scope. The carrier should reserve `job_id` as a potential routing handle (nullable field, present when the turn is associated with a Shape D worker). This is a non-breaking field addition to the carrier spec; it does not change #1778's sequencing or its `blocked-by #1619` status.

---

### D-7: Decisions to ratify (Shape D)

| # | Decision | Architectural position | Product call needed? |
|---|---|---|---|
| **D-D1** | Shape D is a 4th shape, not a composition | Yes — new executor role, new taxonomy row | No — architecture call |
| **D-D2** | `job_id` = durable routing key for Shape D (J2 exception) | Yes — ADR-084 must extend | No — architecture call; confirm with lead |
| **D-D3** | Steer subject = `factory.job.<job_id>.steer` | Yes — subject naming convention consistent with `factory.progress.<job_id>` | No — architecture call |
| **D-D4** | Liveness store = NATS-KV `factory-active-jobs` (not turns.db) | Yes — forced by remote worker constraint | No — architecture call |
| **D-D5** | Rule engine (#475) owns steer-vs-launch; ToolRegistry (#493) is orthogonal → Superseded by the reformulation below: concurrency is a typed worker capability, not an ownership question. | Yes — routing rule scope matches #475's declared scope | No — architecture call |
| **D-D6** | Shape D is Phase 2+; does not block #1777 or #1778 | Yes — no shared code surface with #1777 fix; #1778 carrier needs only a nullable field reservation | Confirm priority with product owner |
| **D-D7** | How the inbound message carries/infers `job_id` (explicit command vs session context) | — | **Yes — product decision required** |

---

### D-8: Not analyzed

The following items are within Shape D's scope but were not groundable in the available artifacts:

| # | Item | Why unanalyzed |
|---|---|---|
| N1 | Shape D worker process lifecycle (startup, graceful shutdown, steer ACK semantics) | No existing worker of this type to read; would need new spec |
| N2 | Whether steer messages are ordered (NATS subject = unordered by default; JetStream needed for ordered delivery) | Depends on steer semantics not yet defined by product |
| N3 | Backpressure / steer queue depth — what happens if the hub sends 10 steers before the worker processes 1 | No existing buffering model for this worker type |
| N4 | Security model for steer subject — any hub process can publish `factory.job.<id>.steer`; ACL scope not designed | Requires ACL matrix extension (see `acl-matrix.json`) |
| N5 | How `job_id` is propagated from the inbound message to the rule engine context (explicit field in InboundContext? derived from session?) | Requires #1778 carrier design to land first; product must also define the user-facing UX |
| N6 | Worker discovery beyond liveness: can a pool query "what Shape D workers are running for this user/bot?" | Not in any existing contract or spec; requires new NATS subject design |

---

### D-5a reformulated — concurrency is a typed worker capability, not an ownership question

> **Superseded/amended by §15 (2026-06-08):** concurrency_mode taxonomy confirmed in §15.3 (steer|queue|parallel); `parallel` mode is not pool-scoped (no pool_id→job_id index entry), distinct from steer/queue which are pool-scoped.

The original D-5a framing ("middleware vs rule engine — who *owns* steer-vs-launch") was a **false dichotomy**: the rule engine (#475) runs **inside** the hub and reads hub config — "middleware" and "rule engine" are both hub code. The real axis is **what drives the decision**: free-form `if-then` rules vs a **typed worker capability**.

**Resolution:** each worker/satellite **declares its concurrency mode** as a typed capability in its contract (`roxabi-contracts`):

```
concurrency_mode : steer | queue | parallel
```

The three modes are the three shapes' concurrency behaviours:

| Mode | Behaviour | Shape | Example satellite |
|---|---|---|---|
| **steer** | worker absorbs the message mid-job, multi-turns itself | **D** | research-worker, multi-turn CLI job |
| **queue** (mono-thread) | message waits, runs as the next turn in order (= existing Pool serialization, `core/CLAUDE.md`) | **A** | conversation pool, voice-worker |
| **parallel** | message is context-independent → fan out a fresh concurrent job | **B** | vault-add (each URL independent), stateless RPC |

The hub reads the **target worker's declared mode** and routes deterministically: `steer` → `factory.job.<id>.steer` · `queue` → enqueue (Pool already serializes/debounces) · `parallel` → spawn a fresh concurrent job.

**Why this reconciles the tension:**
- **Configurable per worker** — the mode is declared per satellite (behaviour genuinely varies by worker).
- **Safe** — the config is a **validated typed enum**, not free-form `if-then`; you can only *pick* a validated mode, never write a malformed rule that swallows a message. Safety comes from the config being **typed**, not from being absent (this corrects the original "hardcode it" stance).
- **Deterministic** — declared mode → fixed routing.
- **#475 stays for genuine content free-choice** (bare-URL → vault-add, auto-respond), NOT the concurrency mode (a worker capability).

**Session vs worker-steer are NOT redundant** (a probing concern worth recording): `session_id` (conversational, Shape A) is **durable** — survives pool eviction / process restart, resumable from `turns.db` cold storage weeks later, addressed via the permanent `pool_id` mailbox. `job_id` (worker, Shape D) is **ephemeral** — valid only while the worker process lives, dead once terminal. A conversation must be addressable **forever** (a mailbox); a worker only **while alive** (a live handle) → not collapsible. The worker is a **child** spawned from a turn within the session (`parent_job_id`), not a peer or replacement. The very existence of the steer-vs-launch decision proves they are distinct. They unify **only** in the liveness/observability dimension (the active-jobs registry counts both as "active work"), while staying distinct as **identities**.

---

## 14. The two orthogonal axes — executor shape ⟂ lifecycle nesting

The shapes A/B/C/D answer *who executes the work*. They do **not** answer *how work nests over the life of a conversation*. These are **two orthogonal axes**; conflating them was the root of the "is this a job or a sub-job?" confusion.

### Axis 1 — lifecycle nesting

| Level | Key | Lifetime | Meaning | Observability |
|---|---|---|---|---|
| **Container** | `pool_id` | permanent | "this Telegram chat ↔ this bot" — the mailbox / address | — |
| **Job** | `session_id` | `/clear`-bounded | a continuous context thread | — |
| **Sub-job** | *turn* (no id today) | one round-trip | inbound→outbound | **drives typing** |

Core claims:
- **`session = job`** (conversational). `/clear` ends the current job and opens a fresh one **in the same container**. Resume (path-1/2/3) = the **join-or-create decision at the session level**: "which job does this incoming message belong to?"
- **turn = sub-job** = one round-trip; it is the unit that drives the typing indicator. **It has no identity today** (no `turn_id`; turns are implicit role-log entries, `message_id` reused as trace) — a gap to fill if typing/observability are to be job-clean.
- **`pool_id` is the address, never the job identity.** A single `pool_id` sees multiple jobs (sessions) come and go across `/clear`. This refines J2: job identity = `session_id`; `pool_id` = routing-to-the-container.

`pool_id` mechanics (verified):
- Format `"{platform}:{bot_id}:{scope_id}"` — canonical via `RoutingKey.to_pool_id()` (`core/hub/hub_protocol.py:142`); never built inline (ADR-001 §4).
- `scope_id` is computed **adapter-side**, opaque to core (`core/messaging/scope.py:9`): Telegram DM → `chat:{chat_id}` (`telegram_normalize.py:27`); shared space (group) → `user_scoped()` appends `:user:{id}` so each user gets their own pool (`scope.py:15-27`); Discord thread → `thread:{thread_id}` (`discord_inbound.py:240`).
- **Reuse = continuity**: `PoolManager.get_or_create_pool(pool_id, …)` keys an LRU dict; same `pool_id` → same `Pool` (with its session + history); idle eviction preserves the session (`core/hub/pipeline/pool_manager.py:42`, `:144`).
- **`/clear` does NOT change `pool_id`** — it rotates `session_id` *inside* the pool (`core/pool/pool.py:243`).

### Axis 2 — executor shape (perpendicular)

The shape describes *what runs beneath a turn*, not the nesting:

```
session (job)
  └ turn (sub-job) ──runs-on──> Shape A (in-process Pool → clipool)        [normal]
       │                          └ Shape C = internal LLM hops (#1490), invisible to user
       └────────delegates───────> Shape D (remote worker, hours)           [the worker-future]
                                    └ may spawn Shape B/C children (parent_job_id)
```

→ **Shape D is not a peer of session/turn — it is an executor a turn delegates to.** A Shape D worker is the **child of a turn** via `parent_job_id`. The "sub-job for typing" the product owner asked about is the **turn**, which belongs to Axis 1 — not Shape D.

### Where reconciliation happens

The join-or-create (which session a message belongs to) is a **hub-side** decision, keyed by `pool_id` (address) → resolving `session_id` (job). **Today it is split**: the inbound layer already resolves `pool_id` and reads last-session (`inbound/session_builder.py:94` and `:98` `get_last_session(pool_id)`), then the hub re-validates via path-1/2/3 (`core/hub/middleware/path_validation.py`). That split is exactly the #1777 multi-SSoT smell. **Target: hub-only** reconciliation; inbound reduced to pure transport + normalization (consistent with D1 — "adapter holds nothing for resume"). Note: 'pure transport' is sharpened below — the adapter still owns address derivation and platform effects; only job/session resolution is hub-only (see *Address derivation, platform effects, and policy*).

### Typing = projection of an active-jobs registry

> **Superseded/amended by §15 (2026-06-08):** full registry schema, use-case table, writer=hub-only rationale, and CLOSE/heartbeat/TTL model specified in §15.3; dashboard/kv.watch staging in §15.5.

The product requirement "know which jobs are in progress" and the typing indicator are the **same fact at different timescales**:

```
turn active   (seconds) → typing dots
Shape D active (hours)  → progress updates
                          SAME registry, SAME observable facet, different scale
```

A degenerate version **already exists**: `TypingPublisher` is ref-counted on `(platform, bot_id, scope_id)` (`transport/typing_publisher.py`) over the `factory.typing.{platform}.{bot_id}` bus — it counts in-flight ops per conversation but does **not** know *which* jobs. Today typing is also **double-driven** (adapter-optimistic `telegram_inbound.py:97` AND hub NATS signal via `typing/listener.py`) — the same multi-source smell as last-session.

**Consistency finding:** the `factory-active-jobs` KV proposed for Shape D steer-discovery (see §13, D-3) is the **same registry** the typing case needs. It is therefore **not Shape-D-specific**, and is needed **earlier** than Phase 2 (typing is a today-problem). Promoting the typing refcount to a job-keyed registry (active `session_id`+turn / worker `job_id`) gives proper typing AND Shape D discovery from one object.

### The six reconciliations (what didn't match, now resolved)

| # | Tension | Resolution |
|---|---|---|
| 1 | executor shapes (A/B/C/D) and nesting (session/turn) were conflated | two orthogonal axes; this section adds the nesting axis |
| 2 | `pool_id` loosely called "the job key" | `pool_id` = address; job identity = `session_id` (refines J2) |
| 3 | reconciliation split inbound (`session_builder`) + hub (`path_validation`) | = #1777 smell; target hub-only, inbound → pure transport |
| 4 | `turn` has no identity (no `turn_id`) | introduce a sub-job identity if typing/observability must be job-clean |
| 5 | no active-jobs registry (only in-process `Pool.is_idle`) | the Shape D `factory-active-jobs` KV = the same registry; needed before Phase 2 |
| 6 | typing double-driven (adapter-optimistic + hub NATS) | collapse to one source: projection of the active-jobs registry |

→ Items 4–6 are the **observability/typing thread**; they are NOT part of #1777 (last-session SSoT) but share the same registry destination.

### Address derivation, platform effects, and policy — the inbound refinement

The "inbound = pure transport + normalization" framing above is **too strong**. The Discord **#links auto-thread** case (a watch-channel that auto-creates a thread and auto-responds to untagged messages) forces a sharper partition of three concerns that were conflated in the single `_discord_pre_session_hook`:

| Concern | Owner | Rationale |
|---|---|---|
| **Policy** (auto-thread / auto-respond-untagged) | **HUB** (`config.db` / rule-engine #475) | a channel-behavior decision — same family as routing |
| **Platform effect** (Discord `create_thread`) | **Adapter** | only the adapter holds the gateway connection; the hub cannot create a Discord thread |
| **Address derivation** (`scope_id` → `pool_id`) | **Adapter** (normalization) | deterministic projection of platform facts; platform-bound |
| **Job/session resolution** (join-or-create, `last_session`) | **HUB only** | the actual smell — the only "everything hub-side" invariant that matters |

Key points:
- **Address derivation is always adapter-side, for every platform** — Telegram DM derives `chat:777`; #links derives `thread:{id}` (`discord_inbound.py:240`). The adapter computing `pool_id` is *normalization*, not the smell. The smell was the inbound **reading a session** (`session_builder.py:98`). The auto-thread path reads **no** session: a fresh thread = a fresh pool → the hub MISSes and creates (`pool.py:105`). So #links already satisfies "no session resolution at the inbound".
- **The genuine improvement:** move the auto-thread / auto-respond **policy** out of adapter-baked config (`_watch_channels`, `_auto_thread`, `discord_inbound.py:135-137`) into **hub-owned config** (rule-engine #475 / `config.db`). The adapter becomes an **effector** that caches a hub-owned policy and applies it synchronously at ingress.
- **Policy-delivery decision:**
  - **(a) Config-push (recommended)** — the hub pushes channel policy to the adapter, applied synchronously at ingress (consistent with `tool_display_config` / typing-factory push per `adapters/CLAUDE.md`). The adapter caches **read-only config**, not session state, so D1 ("adapter holds nothing for resume") still holds.
  - (b) Command round-trip — adapter publishes the raw message, the hub decides and commands `create_thread`. Cleaner ownership, +latency.
- **Why the effect stays at ingress (not outbound):** under D3 (`thread = pool_id`), the thread must exist from message 1 so `pool_id` is thread-derived; otherwise subsequent in-thread replies need a `thread→pool` mapping — exactly the `ThreadStore` session-pointer D3 removes. Creating the thread at ingress yields continuity with no mapping.

**Corrected formulation:**
```
BEFORE (too strong): inbound = pure transport + normalization
AFTER  (exact):      inbound = transport + address derivation (incl. platform effects)
                             + policy effector (hub-owned config)
                     job/session resolution = HUB-only   ← the only "all hub-side" invariant that matters
```

**Concrete #links target flow:**
```
1. INBOUND (adapter): normalize → consult hub-owned policy (cached) → EFFECT create_thread
   → derive scope_id=thread:{id} → pool_id → publish (no session read)
2. HUB: policy auto_respond ⇒ PROCESS (not DROP) even without mention
   → join-or-create(pool_id): fresh thread ⇒ MISS ⇒ Pool mints session_id
3. OUTBOUND (adapter): deliver into thread:{id}
4. CONTINUITY: in-thread reply → adapter re-derives same thread:{id} → same pool_id
   → hub path-3 get_last_session(turns.db) → same session (no ThreadStore-pointer, D3)
```

This **reinforces D-5a**: routing/behavior decisions (auto-respond, auto-thread, steer-vs-launch) are **hub-owned** (config / rule-engine evaluated hub-side); platform **effects** are adapter-side. The same partition holds throughout.

---

## 15. Session convergence (2026-06-08) — job_id=run, unified transport taxonomy, active-jobs registry

All decisions below are **ratified** (supersede open questions in §4/§10/§13/§14 where flagged).

---

### 15.1 — job_id = run (identity pivot, replaces J2 Option 1)

```
let: job  := one entry → full processing (internal multi-turn + steer + streaming) → final closing message
     run  := the atomic unit of observable work (≡ job)
     ¬turn := job is NOT per-turn
```

| Concept | Status |
|---|---|
| `job_id` minted at ingress, per user message | **ratified** — the run is the job |
| Internal LLM hops inside a worker (e.g. cli_pool multi-turn) | NOT job_ids — opaque, irreducible internal layer |
| Sub-jobs | EXPLICIT cross-process decomposition via `parent_job_id` + `composite_depth` only |
| ADR-084:75 "(turn)" label + :81-88 "child jobs" framing | under-ratified context (UNBUILT obs epic #1622); self-contradicts :87 "root job_id minted at ingress per user message"; ratified part of ADR-084 = WorkEnvelope/ContractEnvelope split (Option C) — ¬turn-granularity |

**Consequence:** pool_id/job_id split re-framing
```
pool_id = conversation address / mailbox (≈ Langfuse session; long-lived, reused across runs)
job_id  = run identity (ephemeral, one-per-user-message)
steer   → targets job_id natively — no separate worker_id/steer_target field needed
```

Resolves D-2 (`job_id` overload): no overload — `job_id=run` is durable for the whole run.

---

### 15.2 — ThreadStore dual-role → ship D1 first, J3 second

```
let: D1 := remove path-2 (session persistence via ThreadStore)
     J3 := remove inbound dead-reads (session_builder.py:98/:168)
```

**ThreadStore roles:**

| Role | Methods | Status |
|---|---|---|
| SESSION persistence (path-2) | `get_session`, `update_session` | REDUNDANT — `scope_id=thread:{thread_id}` → `pool_id` 1:1 with thread → `get_last_session(pool_id)` resolves same session |
| OWNERSHIP/routing | `claim`, `is_owned`, `get_thread_ids` | NOT in pool_id, load-bearing for reconnect discovery → KEEP |

**Sequencing:**
```
D1 (remove path-2) ¬= J3 (remove inbound session reads)
→ independently shippable
→ ship D1 first (path-2 removal); J3 (dead-read removal) second
→ touch different methods, different call sites
```

---

### 15.3 — `factory-active-jobs` NATS-KV registry

```
let: registry := factory-active-jobs (NATS-KV bucket)
     key      := job_id
     writer   := HUB-only
```

**Schema:**

| Field | Type | Notes |
|---|---|---|
| `pool_id` | string | owning worker pool |
| `status` | `open \| closing` | — |
| `started_at` | ISO timestamp | — |
| `steer_subject` | string | `factory.job.<id>.steer` |
| `concurrency_mode` | `steer \| queue \| parallel` | see partition below |
| `worker_loc` | string? | optional worker location hint |

**Secondary index:** `pool_id → job_id` (singleton for `steer`/`queue` modes; absent for `parallel`).

**concurrency_mode partition:**

| Mode | Pool-scoped? | Index entry? | Notes |
|---|---|---|---|
| `steer` | yes | yes (pool_id→job_id) | singleton — steer into running job |
| `queue` | yes | yes (pool_id→job_id) | singleton — queue behind running job |
| `parallel` | NO | no | worker fan-out, keyed by job_id only; hors index pool |

concurrency_router INBOUND decision space = {fresh | steer | queue}; `parallel` = worker capability, separate axis.

**Liveness / TTL model:**
```
CLOSE    → terminal JobResult (authoritative, event-driven)
heartbeat → worker heartbeat-message refreshes TTL (FALLBACK only)
TTL reap → safety net for crash-before-terminal OR lost terminal
```

**Cross-process replaces:** `pool._current_task` / `is_idle` + `TypingPublisher._refcount`

---

### 15.4 — Result durability: Option 2

```
terminal JobResult = best-effort (core NATS reply)
WORK     = already durable one layer down:
             turns.db  → JetStream at-least-once
             artifacts → blobstore
```

**INVARIANT:**
```
result    = persisted at data-layer (turns.db / blobstore, durable)
JobResult = notification + status + ref (best-effort) → triggers CLOSE
```

Do NOT promote `factory.results` to JetStream — notification ¬= data.

---

### 15.5 — Dashboard / real-time follow: staging v1/v2

```
v1 = kv.watch(factory-active-jobs)   ← coarse live board; registry IS JetStream-backed
v2 = factory.job.> stream            ← timeline / replay / high-freq (obs/trace plane)
```

| Concern | Mechanism | Phase |
|---|---|---|
| Live job board | `kv.watch(factory-active-jobs)` | v1 |
| Replay / trace / high-freq | JetStream stream on `factory.job.>` | v2 |
| Dashboard feed | `kv.watch` | v1 (sufficient for coarse board) |

Note: `kv.watch` needs ephemeral-consumer ACLs (see NATS-KV cold-boot memory).

Do NOT promote `factory.results` to JetStream.

---

### 15.6 — Unified subject taxonomy `factory.job.<job_id>.<facet>`

```
factory.job.<id>.steer    ← steer control channel
factory.job.<id>.result   ← terminal result (pub/sub, hub subscribes — ¬block-await)
factory.job.<id>.progress ← progress events (high-freq, pub/sub)
factory.job.<id>.*        ← full job subtree (captured by JetStream stream, v2)

factory.jobs.<job_name>   ← dispatch QUEUE (by type, fan-in, queue-group 1-of-N)
```

**Supersedes:**
```
factory.results.<job_id>   → factory.job.<id>.result
factory.progress.<job_id>  → factory.job.<id>.progress
```

**Disambiguation:**
```
plural  jobs  = queue (dispatch, fan-in)
singular job  = instance (per-id facets)
```

`result` pub/sub on STATIC subject `factory.job.<id>.result` — hub subscribes, not block-await.

---

### 15.7 — Retention

```
JetStream stream on factory.job.> captures ALL facets (incl. high-freq progress).
Retention / compaction → managed independently, later.
¬block v1 on retention policy decisions.
```

---

### 15.8 — Edges stay platform-keyed

```
inbound/outbound  = pub/sub TODAY
edges             = STAY keyed by <platform>.<bot_id>
correlation       = job_id travels IN the envelope (WorkScope + job_id, ADR-084)
```

No rename of edge subjects. Job correlation is envelope-carried, not subject-carried.

---

### 15.9 — Unified transport taxonomy (three tiers)

| Tier | Mechanism | Use cases |
|---|---|---|
| **at-most-once** | core NATS pub/sub | inbound, outbound, typing, progress, `.result` notification, `.steer` |
| **at-least-once** | JetStream (persisted, PubAck) | `turns.write`, dispatch queue (`factory.jobs.<name>`) |
| **KV** | JetStream-backed KV | sessions, `pool_id→job_id` index, `factory-active-jobs` registry |
| **request-reply** | NATS RPC | current driven-port calls — MIGRATING to sub-jobs (§15.10) |

---

### 15.10 — STT/LLM/TTS/image = sub-jobs under unified job model

```
let: sub-job := cross-process pipeline stage with parent_job_id
     intra-worker hop := ¬job (opaque — e.g. cli_pool internal Claude multi-turn)
```

**Boundary requalification:**
```
BEFORE: driven-ports = RPC
AFTER:  intra-worker hop = ¬job (opaque, irreducible)
        cross-process stage = sub-job on factory.job.<id>.* (pub/sub, observable, steerable)
```

**Voice→voice job tree example:**
```
J (run)
  └─ sub-job: STT       (parent_job_id=J, factory.job.<stt_id>.*)
  └─ sub-job: cli       ← internal Claude multi-turn = NOT jobs (opaque)
  └─ sub-job: TTS       (parent_job_id=J, factory.job.<tts_id>.*)
  └─ close J
```

**Ports survive:** `LlmProvider` / `TtsProtocol` / `STTProtocol` in `core/ports/` — only transport adapter migrates RPC → job-pub/sub.

**Observable:** sub-jobs on `factory.job.<id>.*` = pub/sub, steerable, captured by v2 stream.

---

### 15. Summary table

| Thread | Decision | Supersedes |
|---|---|---|
| §15.1 | `job_id=run`; intra-worker hops = opaque; sub-jobs = explicit via `parent_job_id` | §4 J2 Option 1, §13 D-2 |
| §15.2 | D1 (path-2 removal) ¬= J3 (dead-read removal); ship D1 first | §10 J3 block |
| §15.3 | `factory-active-jobs` KV: schema, index, writer=hub, liveness/TTL model, `parallel` ¬pool-scoped | §13 D-3, §13 D-5a, §14 active-jobs registry |
| §15.4 | Result durability = Option 2 (best-effort notify, durable data layer) | — |
| §15.5 | Dashboard v1=`kv.watch`, v2=`factory.job.>` stream; ¬promote results to JetStream | §14 active-jobs registry |
| §15.6 | Unified `factory.job.<id>.<facet>` taxonomy; supersedes `factory.results.*` + `factory.progress.*` | §13 D-3 |
| §15.7 | JetStream stream on `factory.job.>` captures all; retention later | — |
| §15.8 | Edges stay `<platform>.<bot_id>`; job_id carried in envelope | — |
| §15.9 | Three transport tiers: at-most-once / at-least-once / KV / RPC→migrating | — |
| §15.10 | Cross-process stages = sub-jobs; intra-worker = opaque; ports survive | — |
