# WorkEnvelope — the job_id invariant (Roxabi Factory, plan A)

> Captured 2026-06-01. Design decision, pre-ADR. Source-of-truth for the job-id wire rule.
> Context: `~/projects/docs/vision-roxabi-factory.md` (Factory = distributed-first / plan A, harness independent).
> Status: **decided** (envelope split + ingress mint + request_id fold). Spec → issues pending.

## Decision in one line

Every **work** message on the bus carries `job_id` (+ `parent_job_id`), enforced **by type**
via a `WorkEnvelope` subclass. Infra messages (heartbeat, health, lifecycle) stay on bare
`ContractEnvelope` and carry only `trace_id`. "Every message carries a jobID" was *not*
realistic literally — heartbeats belong to no job — but is realistic and enforceable for
work messages.

## Why (grounded in current source)

| Fact | Source |
|---|---|
| `ContractEnvelope` = base of **every** NATS model; `trace_id` is mandatory (`min_length=1`) | `packages/roxabi-contracts/.../envelope.py` |
| `trace_id` is therefore already on **100%** of transiting messages | all domains extend `ContractEnvelope` |
| `job_id` exists **only** in `jobs/` (and incidentally `gh/`) | grep `job_id` |
| Heartbeats extend `ContractEnvelope` → carry `trace_id` but belong to no job | `CliHeartbeat`, `ImageHeartbeat` |
| Satellite RPCs carry `trace_id`, not `job_id` | `voice/`, `llm/`, `image/` models |
| A 3rd id already floats: `request_id` ("trace_id falls back to request_id") | `voice/builders.py`, `llm/builders.py` |

→ The universal "everything is correlated" invariant **already exists** as `trace_id`.
What plan A adds is `job_id` on the **work** subset.

## Id model (= OpenTelemetry)

```
trace_id       constant across the whole request tree   ("everything is linked")
job_id         one node = one span = the "runID"
parent_job_id  the tree edge (nesting)
→ one trace contains N job_id. Already present together in JobEnvelope.
```

`trace_id` → Langfuse `trace_id` · `job_id` → span · `pool_id` → Langfuse `session_id`
(aligns with obs epic #667).

## Envelope hierarchy

```
ContractEnvelope            trace_id (mandatory), contract_version, issued_at
   │                        ← INFRA: heartbeat, health, lifecycle, metrics
   └─ WorkEnvelope          + job_id (mandatory), parent_job_id (nullable: None at root)
                            ← WORK: llm, voice, image, turns, jobs, harness, inbound/outbound
```

- `WorkEnvelope.job_id` : `min_length=1`, required → presence guaranteed by the type.
- `WorkEnvelope.parent_job_id` : `str | None`; `None` only at the root span.
- `job_id` / `parent_job_id` are **not** security-bearing → additive minor bump is safe
  (envelope `extra="ignore"` forward-compat holds; ADR-049 rules respected).

## Classification rule (per-model pass required)

Default: **a message that belongs to a unit of work → `WorkEnvelope`. A message about a
component's own liveness/lifecycle → `ContractEnvelope`.**

| Domain / model | Envelope | Note |
|---|---|---|
| `jobs/*` (JobEnvelope/Result/Progress) | WorkEnvelope | already has the fields; reparent to base |
| `llm/` LlmRequest/Response/ChunkEvent | WorkEnvelope | harness ↔ clipool / LLM-worker |
| `llm/` LifecycleRequest/Response | ContractEnvelope | worker lifecycle = infra |
| `voice/` Tts/Stt Request/Response | WorkEnvelope | satellite work |
| `image/` request/response | WorkEnvelope | satellite work |
| `image/`/`cli/` *Heartbeat | ContractEnvelope | infra — the load-bearing exemption |
| `cli/` CliControlCmd, CliCmdPayload | WorkEnvelope | per-turn clipool dispatch; carries `lyra_session_id` → coordinate cli bump with **#1009** |
| `turns/` TurnWriteEvent | WorkEnvelope | belongs to the turn-job |
| `event/` LyraEvent, LyraMetric | ContractEnvelope | observability/infra |
| harness wire (#1490, new) | WorkEnvelope | the turn-job |
| inbound/outbound messages | WorkEnvelope | inbound = root span; outbound = same job |
| `audit/` | TBD | per-model — audit provenance may want job correlation |

## Two correctness points

1. **Root job_id minted at ingress.** The inbound adapter (Telegram/Discord receipt) mints
   `trace_id` + root `job_id` (`parent_job_id = None`) the moment a user message enters.
   It then flows: `inbound → hub → harness → llm/voice/image worker (child job) → outbound`,
   the whole tree sharing one `trace_id`.
2. **Fold `request_id` → `job_id`.** Voice/LLM builders already carry a `request_id` with a
   "trace_id falls back to request_id" rule. Keep the system to **two** ids (trace_id, job_id);
   `request_id` collapses into `job_id`. Avoids trace_id + request_id + job_id triplication.

## Enforcement

- Type-level: work subjects deserialize to `WorkEnvelope` subclasses → `job_id` cannot be absent.
- Test-level: a subject→envelope mapping test asserts every `lyra.jobs.*` / work subject uses a
  `WorkEnvelope` model and every infra subject (`*heartbeat*`, lifecycle) uses bare
  `ContractEnvelope`. Same enforcement spirit as the existing importlinter contracts.

## "Realistic?" — verdict

| Formulation | Realistic |
|---|---|
| Every **work** message carries `job_id` (+parent +trace) | ✅ yes, by type (`WorkEnvelope`) |
| **Literally every** message (heartbeat included) carries `job_id` | ❌ no — infra has no job |
| Every message carries `trace_id` | ✅ already true today |

## Next

- [x] Promote to an ADR — **ADR-084**.
- [ ] Spec the `roxabi-contracts` change: add `WorkEnvelope`, reparent work models, version bump — **#1619**.
- [ ] Per-model classification pass (the TBD rows above) — part of #1619.
- [ ] Ingress-mint change in inbound adapters — **#1620** · `request_id` → `job_id` fold — **#1621**.
- [ ] Subject→envelope enforcement test — part of #1619.
- [ ] Wire `job_id` as OTel span id — **#1623** under obs epic **#1622** (¬ #667, superseded).
- [ ] Coordinate the `cli`-domain bump with the session-id cleanup (**#1009**) — overlapping contracts minor bump; reconcile the id taxonomy (`cli_session_id` / `lyra_session_id` / `pool_id` / `job_id` / `trace_id`).
