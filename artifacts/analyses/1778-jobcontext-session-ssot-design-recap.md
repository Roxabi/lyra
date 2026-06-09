# Session-SSoT → Job-Context → Rule-Engine — Design Recap & Open Questions

> **SUPERSEDED → `docs/architecture/job-model.md`** (living current-truth, graduated 2026-06-08).
> Brainstorm-stage recap of the #1777/#1778 design; the unified job model has since been
> ratified and graduated into the domain page. Frozen historical record — do not edit;
> consult the domain page for current truth.

- **Date:** 2026-06-08
- **Scope:** #1777 (last-session SSoT bundle) · #1778 (In-process JobContext epic) · #1619 (WorkEnvelope) · #475/#493 (rule engine)
- **Status:** brainstorm captured — design NOT finalized; 13 open questions below block the ADR
- **Trigger:** 2026-06-05 P0 inbound outage (#1721 colon-key) exposed the multi-SSoT smell behind a broken `/clear`

---

## 1. The problem — root smell

`/clear` is broken because there are **3 disjoint sources of truth** for "the last session of a pool", written by 3 processes, read by 3 resume paths:

| Store | Backend | Writer | Read by | Cleared by `/clear`? |
|---|---|---|---|---|
| `pool_sessions` | `turns.db` SQLite | turn-writer (ADR-075, **sole writer**) | hub (path-3) | ✅ self-heals via `start_session` INSERT |
| `KvLastSessionStore` | NATS-KV `factory-turns-meta` | **adapter** (Telegram / DM) | adapter → injects `thread_session_id` → hub (path-2) | ❌ stale → **resurrection** |
| `ThreadStore` | `discord.db` | **adapter** (Discord thread) | adapter (path-2) | ❌ stale |

`MessageIndex` KV (msg→session, path-1) is a **separate fact** (reply-to mapping), not a duplicate of last-session.

## 2. Mechanics — why `/clear` doesn't work

Resume paths, in priority order:

```
path-1 reply-to      ← MessageIndex KV (msg→session)        [explicit user intent]
path-2 thread-resume ← adapter-injected thread_session_id   [scope-validated same-pool]
path-3 last-active   ← turns.db get_last_session(pool_id)   [gated: only if pool idle]
```

`cmd_clear` → `pool.reset_session()` rotates the in-memory session UUID and publishes `start_session` to NATS, **but clears no store**. The `LastSessionStore` port has only **get/set, no delete**. So on the next message:

- turns.db (path-3) **self-heals** — the `start_session` event makes turn-writer INSERT a fresh `pool_sessions` row → `get_last_session` returns the new session. The real SSoT was already correct.
- the **adapter mirror** (path-2: KV for Telegram/DM, ThreadStore for Discord) is **never cleared** → still points at the pre-clear session → because path-2 sits **above** path-3, it **resurrects the cleared session**.

**The adapter mirror is the liar.** Killing it (D1 below) fixes `/clear` with no new clearing logic needed on turns.db.

## 3. Locked decisions (this brainstorm)

| # | Decision | Rationale |
|---|---|---|
| **D1** | **Fix = ownership consolidation.** turn-writer is the sole writer; the adapter keeps **nothing** for resume; all resume reads resolve against one source, behind the swappable `LastSessionStore` port. | turns.db is already canonical; the adapter KV/ThreadStore session-pointers are redundant mirrors of what the turn log records. |
| **D2** | **Backend = SQLite now.** Not NATS-KV, not Redis. | KV is JetStream-stream-backed (get = `$JS.API.STREAM.MSG.GET`, FILE-backed fsync) → **more** overhead than local indexed SQLite; its only edge is network-accessibility. Verified via 7-agent workflow; skeptic refuted=false. KV/Redis only become relevant for a multi-machine hub. |
| **D3** | **Discord thread = its own pool** (`scope_id = thread:{thread_id}`). | turns.db `pool_sessions` is **already** the thread's last-session SSoT → path-3 handles thread resume → path-2 is redundant (it scope-validates same-pool) → **removable**. ThreadStore keeps ownership/claim, **drops** the session-pointer. |
| **D4** | **#1777 ships FIRST, independent of #1619.** | The ownership-consolidation fix is target-agnostic — it's the foundation for *any* backend or carrier — and it fixes `/clear` now. Folding the *direction* into the carrier epic must NOT gate the bug-fix behind the unimplemented WorkEnvelope. |

## 4. The target — 3-layer model

```
data layer          →   carrier layer              →   decision layer
#1777                   #1778 In-process JobContext     #475 / #493
last-session SSoT       (job_id + session + routing)    rule engine
"which store"           "the per-turn envelope"         "rules evaluated against it"
                              ▲
                              │ blocked-by
                        #1619 WorkEnvelope (wire job_id, ADR-084) — UNIMPLEMENTED
```

- **Carrier fills a real gap:** today `InboundContext` (ingress-side composite) and `PipelineContext` (hub-side mutable) have **no bridge**. #1619 supplies wire identity; the carrier threads it in-process.
- **ADR-073 (stage-axis decomposition)** says each pipeline stage owns its own context → the carrier must be a **thin read-only composing envelope**, not a monolith that replaces per-stage contexts. This tension needs explicit ADR resolution (axial-review will flag it).

## 5. Issue landscape — actions taken

- **#1778 epic created** (`epic` / size L / P1) — "In-process JobContext (unified hub turn-context)".
  - children: **#1777** (last-session SSoT, first child, **not blocked**) + **#1731** (SessionCtx cleanup — *rescued from #1049 which is CLOSED*).
  - `blocked-by #1619`; body cross-refs #475/#493/#1490.
- **Supersede pass — 0 closures.** #1044 (worker-fleet) vs #1490 (harness) are **parallel-distinct** (harness = agent-turn/LLM loop; worker-fleet = code-jobs vault/web-intel/composites; shared JOBS bus ≠ replacement). #475/#493 are **live** (last active 2026-06-05), not stale. #1045 already CLOSED (no-op). The "old epics to close" premise was **data-refuted twice**.
- **Plan correction:** #1053 stays under #1044 (it's the terminal `remove VaultAddProcessor → lyra.jobs.vault.add_from_url` cleanup), NOT reparented under #475.

## 6. Impacts

| Layer | Impact |
|---|---|
| **ADR** | New SSoT-consolidation ADR (cross-layer inbound↔core↔infra → **axial-review mandatory**); must **reconcile ADR-073** (stage-axis vs carrier); relates ADR-075 / ADR-084. |
| **Ports** | `LastSessionStore`: add `clear_last_session(pool_id)` — *or* collapse the port entirely (open Q6). |
| **Hub code** | `reset_session()` → clear; **path-2 removed**; path priority re-stated; `--resume` failure → fresh-session fallback. |
| **Adapter code** | drop `KvLastSessionStore` resume use; `ThreadStore` reduced (keep `claim`, drop `update_session`/session-pointer); rewrite `session_builder._build_thread_path`. |
| **Deploy** | idempotent one-time migration for the `lyra→factory` cwd rename (orphaned claude-CLI session dirs). |
| **Wire** | #1619 WorkEnvelope = prerequisite for in-process `job_id` threading (but NOT a prerequisite for the #1777 fix). |

Code anchors (from prior investigation — **re-verify at impl time**, may have drifted):
`turn_store_queries.py` `get_last_session` · `pool.py` `reset_session()` · `turn_writer/writer.py` `_handle_start_session` · `core/hub/middleware/path_validation.py` path-1/2/3 · `core/commands/workspace_commands.py` `cmd_clear` · `core/ports/last_session_store.py` (get/set only) · `bootstrap/wiring/last_session_wiring.py` `TurnStoreLastSession.set_last_session` (no-op) · `infrastructure/stores/turn_session_kv.py` `KvLastSessionStore` · `adapters/discord/discord_inbound.py` `thread:{id}` scope · `infrastructure/stores/thread_store.py` · `inbound/session_builder.py` · `inbound/context.py` `InboundContext` · `core/hub/middleware/middleware.py` `PipelineContext`.

---

## 7. Open design questions (NOT yet clarified)

Grouped and numbered for one-at-a-time resolution. **G3 + G4 block the #1777 ADR.**

### G1 — Carrier object shape
- **Q1** Does the carrier wrap `InboundContext` + `PipelineContext`, or replace them? Immutable vs mutable?
- **Q2** How does it reconcile ADR-073 (per-stage contexts) without violating it — read-only view? frozen envelope?

### G2 — Identity model
- **Q3** Relationship between `job_id` ⟂ `session_id` ⟂ `pool_id` ⟂ `trace_id` ⟂ `cli_session_id`? How many `job_id`s per turn / message / session?
- **Q4** Where is `job_id` minted — adapter (ingress) or hub? (#1619 is wire-level; what about in-process?)
- **Q5** Is the `cli_session_id ↔ cwd` coupling (root of the rename bug) intended design or an accident to decouple?

### G3 — Store / port — **blocks #1777 ADR**
- **Q6** Does the `LastSessionStore` port survive (add `clear`), or collapse (hub queries turns.db directly)?
- **Q7** With path-2 removed, what exactly happens to `session_builder._build_thread_path`?
- **Q8** `ThreadStore` reduced surface: precisely what remains (keep `claim`; drop session-pointer)?

### G4 — `/clear` & resume semantics — **blocks #1777 ADR**
- **Q9** **reply-to vs /clear conflict:** if a user replies to a *pre-clear* message, does it resume (explicit intent) or respect the clear? ← genuine product decision.
- **Q10** How is "CLI `--resume` failed" detected (empty reply? exit code?) and should it also evict the dangling pointer?

### G5 — Multi-machine future
- **Q11** What is the actual **forcing function** for splitting the hub (#639/#641 Redis? #1044 worker-fleet?)? When turns.db SQLite is no longer FS-shared, which state MUST become network-accessible — last_session? message_index? pool active-state?

### G6 — Rule-engine intersection
- **Q12** Does the **path-priority itself** (path-1/2/3) become rule-engine-driven (#475) — configurable resume rules? Or does the rule engine only touch routing?

### G7 — WorkEnvelope #1619
- **Q13** Is ADR-084 fully specced or does it need completion? What is the **minimal #1619** needed to unblock the carrier (and the orphaned #1623 job_id→OTel)?

---

## 8. Sequencing

```
PHASE 0 (now, unblocked)   #1777 fix → /clear works, ownership consolidated
   └─ REQUIRES G3 + G4 (Q6→Q10) resolved BEFORE the ADR
PHASE 1 (after #1619)      carrier #1778: thread job_id in-process
PHASE 2                    rule-engine #475 evaluates against carrier (if Q12 = yes)
```

**Reality check:** #1777's ADR cannot start without resolving at least **G3 + G4** — otherwise we'd be coding a consolidation whose `/clear` and reply-to semantics are undefined.

## 9. Path to resolution

1. Resolve **G3 + G4 (Q6→Q10)** first — DP per cluster — they unblock the #1777 ADR.
2. `/frame #1777` once G3+G4 are settled → product-lead structures the ADR + spec.
3. Remaining clusters (carrier shape / multi-machine / rule-engine = F-full) → separate `/frame` against #1778.

## References

- P0 post-mortem: memory `project-natskv-poolid-colon-key-outage`; fix PR #1776; introduced by #1721
- ADR-073 (stage-axis decomposition) · ADR-075 (turn-writer sole writer) · ADR-084 (WorkEnvelope)
- Issues: #1777, #1778, #1619, #1731, #475, #493, #1490, #1044
- Related analyses: `workenvelope-job-id-invariant.md`, `harness-epic-consolidated.md`
