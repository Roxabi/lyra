---
title: Job Model — factory
description: Living current-truth document for the unified job model — job_id=run, lifecycle, active-jobs registry, factory.job.<id>.* taxonomy, transport tiers, sub-jobs, and runtime control.
---

# Job Model — factory

> Status: LIVING — current truth for the unified job model and runtime control design.
> Last updated: 2026-06-08.
> Source: ADR-084; ADR-045; ADR-049; ADR-075; artifacts/analyses/job-model-concept-analysis.md §15

## Scope

This document defines what a job is, its lifecycle, the identity model, the unified NATS subject
taxonomy, transport-tier contracts, the active-jobs registry, concurrency routing, sub-jobs, and
observability hooks. It does not cover security/ACL policy (see `security-routing.md`), cross-project
contract schemas (see `contracts.md`), storage layer (see `storage.md`), or NATS infrastructure
(see `messaging.md`).

---

## What a job is

```
job_id = run
run    = one entry → full processing (steer + streaming) → terminal closing message
```

A **job** is the atomic unit of observable work. It is minted at ingress — one per user message —
and spans the entire processing cycle through to the closing **JobResult**. It is **not** a turn.

| Unit | Identity | Scope |
|---|---|---|
| Pool (conversation mailbox) | `pool_id` | Permanent; reused across many jobs |
| **Job (run)** | `job_id` | One user message → full processing → close |
| Sub-job (cross-process stage) | `job_id` + `parent_job_id` | Child of a job; explicit only |
| Internal LLM hop | none | Opaque; e.g. cli_pool internal Claude multi-turn |

Internal worker LLM hops (e.g. cli_pool multi-turn) are **not** jobs — they are opaque and
irreducible inside the worker process. Cross-process decomposition uses `parent_job_id` and
`composite_depth` (max 3). This dissolves the "job_id overload": `job_id` is the durable
routing handle for the entire run; no separate worker_id or steer_target field is needed.

---

## Lifecycle

```
entry (message arrives)
  │
  ▼
processing  ←──── steer signals (factory.job.<id>.steer)
  │         ←──── stream chunks out (factory.outbound.*)
  ▼
terminal close  ──── JobResult published (authoritative, event-driven)
```

| Event | Mechanism | Role |
|---|---|---|
| Open | `job_id` minted at ingress, registry entry created | Start |
| Processing | compute + streaming out; steer signals absorbed | Active |
| Close | terminal **JobResult** published → hub closes the job | Authoritative |
| Heartbeat | worker refreshes TTL on registry entry | Fallback only |
| Reap | TTL lapse on registry entry | Safety net (crash/lost terminal) |

CLOSE is event-driven (terminal **JobResult**). Heartbeat and reap are TTL-based fallbacks
for crash-before-terminal or lost-terminal scenarios only.

---

## Identity model

| Identity | Lifetime | Role |
|---|---|---|
| `trace_id` | request tree | OTel correlation root; spans the whole call tree |
| `pool_id` | permanent | Conversation address / mailbox (≈ Langfuse session); keyed `{platform}:{bot_id}:{scope_id}` via `to_pool_id()` |
| `job_id` | one run | Node/span within the trace; durable routing handle for the whole run |
| `parent_job_id` | per sub-job | Links a sub-job to its parent run |

`pool_id` is the address (long-lived, stable across `/clear`). `job_id` is ephemeral — valid
only while the run is live. A `/clear` rotates the session inside the same pool; it does not
change `pool_id`.

**ADR-084** defines the **WorkEnvelope** / **ContractEnvelope** split and the id taxonomy.
The "(turn)" id-model prose in ADR-084 is amended by #1794: `job_id=run` is the ratified
granularity; turn-granularity id-ing was under-ratified context from the unbuilt obs epic #1622.

---

## Subject taxonomy

```
factory.job.<job_id>.result    ← terminal result (pub/sub; hub subscribes — not block-await)
factory.job.<job_id>.progress  ← progress events (high-freq, pub/sub)
factory.job.<job_id>.steer     ← steer control channel (caller → worker)
factory.job.<job_id>.opened    ← lifecycle open event
factory.job.<job_id>.closed    ← lifecycle close event
factory.job.<job_id>.*         ← full job subtree (captured by JetStream stream v2)

factory.jobs.<job_name>        ← dispatch QUEUE (by type, fan-in, 1-of-N queue group)
```

**Disambiguation:** plural `factory.jobs.*` = dispatch queue (by worker type); singular
`factory.job.*` = per-instance facets.

**Supersedes:**

| Old subject | New subject |
|---|---|
| `factory.results.<job_id>` | `factory.job.<job_id>.result` |
| `factory.progress.<job_id>` | `factory.job.<job_id>.progress` |

Edge subjects stay platform-keyed (`factory.inbound.<platform>.<bot_id>`,
`factory.outbound.<platform>.<bot_id>`). Job correlation travels **in the envelope**
(**WorkEnvelope** + `job_id` field per ADR-084) — not in the subject token.

---

## Transport tiers

| Tier | Mechanism | Use cases |
|---|---|---|
| **at-most-once** | Core NATS pub/sub | inbound, outbound, typing, progress, `.result` notification, `.steer` |
| **at-least-once** | JetStream (persisted, PubAck) | `turns.write`, dispatch queue (`factory.jobs.<name>`) |
| **KV** | JetStream-backed KV | sessions, `pool_id`→`job_id` index, active-jobs registry |

**Result-durability invariant (Option 2):** result *data* is persisted at the data layer
(`turns.db` via JetStream / ADR-075; artifacts via blobstore) — durable regardless of the
notification. The terminal **JobResult** is a best-effort notification + status + ref that
triggers CLOSE. Do NOT promote `factory.job.<id>.result` to JetStream — notification ≠ data.

---

## Active-jobs registry

Cross-process replacement for in-hub `pool._current_task` / `is_idle` and
`TypingPublisher._refcount`.

**Bucket:** **factory-active-jobs** (NATS-KV, JetStream-backed)

| Field | Type | Notes |
|---|---|---|
| key | `job_id` | Per-run registry entry |
| `pool_id` | string | Owning conversation mailbox |
| `status` | `open` \| `closing` | — |
| `started_at` | ISO timestamp | — |
| **steer_subject** | string | `factory.job.<id>.steer` |
| **concurrency_mode** | `steer` \| `queue` \| `parallel` | Pool-scoped routing mode |
| **worker_loc** | string? | Optional worker location hint |

**Writer = HUB only.** Workers may NOT write directly to the registry.

**Secondary index:** `pool_id` → `job_id` SINGLETON — enforced for `steer` and `queue`
modes (one active job per pool at a time). Absent for `parallel` mode.

**Liveness model:**

```
CLOSE event  → authoritative terminal (event-driven, removes entry)
heartbeat    → TTL refresh on entry (fallback only — worker alive = heartbeat)
TTL reap     → safety net for crash-before-terminal or lost terminal
```

---

## Concurrency

The hub's inbound router is ONE shared module (axial N×M — no per-platform copy; same
rationale as the typing factory / ADR-073). Workers declare a **concurrency_mode** as a typed
capability in their contract.

**Guardrail:** the **concurrency_router** is a single inbound stage living under `factory/inbound/`,
bound by each adapter via `functools.partial` (same pattern as `pre_route_hook` / `pre_session_hook`).
An `.importlinter` contract (like `inbound-no-adapters`, #1287) forbids importing it from
`factory.adapters` — it must never become a per-adapter copy.

**Axis primacy:** module decomposition follows the **stage axis** (lifecycle: parse → route →
session → dispatch), NOT the executor-shape axis (Shapes A/B/C/D). Executor shape is a runtime
classification injected via strategy/protocol objects into the single shared stage — it is not a
code-organization boundary.

| Mode | Pool-scoped? | Index entry? | Behaviour |
|---|---|---|---|
| `steer` | yes | yes (`pool_id`→`job_id`) | Inbound steers the running job; singleton per pool |
| `queue` | yes | yes (`pool_id`→`job_id`) | Inbound waits; runs as next turn in order; singleton per pool |
| `parallel` | NO | no | Context-independent → fan-out a fresh concurrent job per message |

The router decision space at ingress is `{fresh | steer | queue}`. `parallel` is a worker
capability (separate axis — the worker is always dispatched fresh).

---

## Sub-jobs

```
cross-process pipeline stage  = sub-job   (pub/sub on factory.job.<id>.*, observable, steerable)
intra-worker LLM hop          = opaque    (not a job — e.g. cli_pool internal Claude multi-turn)
```

A sub-job is an explicit cross-process decomposition: it carries its own `job_id` and a
`parent_job_id` linking it to the parent run. `composite_depth` ≤ 3.

**Voice→voice job tree example:**

```
J  (root run, job_id=J)
  └─ sub-job: STT   (parent_job_id=J, factory.job.<stt_id>.*)
  └─ sub-job: TTS   (parent_job_id=J, factory.job.<tts_id>.*)
  └─ internal: cli_pool Claude multi-turn  ← NOT a sub-job (opaque)
  └─ close J
```

**Ports survive:** `LlmProvider`, `TtsProtocol`, `STTProtocol`, `AuditSink` in `core/ports/`
are unchanged. Only the transport adapter migrates from request-reply to sub-job pub/sub.
Sub-jobs are observable and steerable via the `factory.job.<id>.*` subtree.

**Guardrail:** the job pub/sub transport is a SINGLE shared primitive in `factory.transport`
(one publish/subscribe mechanism) that each port's wiring composes — NOT a per-port NATS
implementation. Rolling separate subscribe/publish logic per adapter
(LlmJobAdapter + TtsJobAdapter + SttJobAdapter + ImageJobAdapter) is the N×M trap (ADR-073).
Precedent: OutboundAdapterBase defines the streaming mechanism once; per-platform adapters
override only the formatter.

---

## Observability / dashboard

The **factory-active-jobs** registry is JetStream-backed — it is the source of truth for
live job state.

| Feed | Mechanism | Notes |
|---|---|---|
| v1 (coarse live board) | `kv.watch(factory-active-jobs)` | Requires ephemeral-consumer ACLs (vs `kv.get` zero-ACL per #1572) |
| v2 (trace / replay / high-freq) | Subscribe factory.job.> (JetStream stream) | Full subtree; retention managed independently |

The job subtree (`factory.job.<id>.*`) IS the trace — no separate observability plane needed.

---

## Implementation status

| Epic / Issue | Shape | Description | Status |
|---|---|---|---|
| #1044 | B | Worker fleet (Shape B — stateless code-worker) | In progress |
| #1778 | A | In-process **JobContext** carrier | Blocked by #1619 |
| #1792 | D | Shape D — steerable stateful + runtime control | Blocked by #1778, #1619, #1203 |
| #1793 | A | Unify subject taxonomy (`factory.job.<id>.*`) | Leaf — unblocked |
| #1794 | B | Amend ADR-084 → `job_id=run` | Leaf |
| #1795 | E | **JobResult** → pub/sub + 3-tier transport | Leaf |
| #1796 | C | Active-jobs registry (NATS-KV **factory-active-jobs**) | Done (substrate) — this PR; live open/close call-sites → #1797 |
| #1797 | D | Concurrency router (shared inbound stage) | Leaf |
| #1798 | F | STT/LLM/TTS/image → sub-jobs | Leaf |
| #1799 | G | Steer e2e | Leaf |
| #1800 | H | Dashboard (v1 `kv.watch`, v2 stream) | Leaf |

**Blocked-by chains:**

```
#1793 (A: taxonomy) → #1795 (E: result pub/sub)
#1794 (B: amend ADR-084) → #1796 (C: registry)
#1778 (Shape A: JobContext) → #1796 (C: registry)
#1796 (C: registry) → #1797 (D: router) → #1799 (G: steer)
#1796 (C: registry) → {#1799 (G: steer), #1800 (H: dashboard)}
#1795 (E) + #1796 (C) → #1798 (F: sub-jobs)
Epic #1792 blocked-by #1778 / #1619 / #1203
```

---

## See also

- `docs/architecture/adr/084-workenvelope-job-id-invariant.mdx` — historical why; **WorkEnvelope** / **ContractEnvelope** split; id-model prose amended by #1794
- `artifacts/analyses/job-model-concept-analysis.md` — full design history; §15 = ratified session (this page distills it)
- ADR-045 / ADR-049 — roxabi-nats SDK and contract schemas
- ADR-075 — `turns.db` JetStream persistence (result data layer)
- `docs/architecture/contracts.md` — cross-project contract schemas
- `docs/architecture/messaging.md` — NATS planes, subject naming, hub dispatch
- `docs/architecture/storage.md` — thread/session stores, KV details

---

## ADR archive

| ADR | Status | Topic |
|---|---|---|
| ADR-084 | Active | **WorkEnvelope** / **ContractEnvelope** split; id-model (`job_id` amended by #1794) |
| ADR-045 | Active | roxabi-nats SDK transport contracts |
| ADR-049 | Active | roxabi-contracts schema registry |
| ADR-075 | Active | `turns.db` JetStream persistence |
