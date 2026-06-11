# Cross-Spec Reconciliation — Batch 2 Parallel Prep

**Date:** 2026-06-11  
**Issues covered:** #1620, #1793, #1009+#1838, #1840 (3 satellites), #1812  
**Branch base:** `staging` @ 37a38621 (post-Batch-1: PRs #1842/#1843/#1845/#1847 all merged)  
**Contracts baseline:** `roxabi-contracts 0.9.0`, `CONTRACT_VERSION = "1"`, `subjects.py` confirmed pre-#1793 (old `factory.results.*` / `factory.progress.*`)

---

## 1. File-Collision Matrix

Legend — Severity: 🔴 BLOCKER | 🟡 MEDIUM (sequencing required) | 🟢 LOW (additive/orthogonal)

### packages/roxabi-contracts/

| File | #1793 | #1009+#1838 | #1812 | #1620 | Severity | Note |
|------|-------|-------------|-------|-------|----------|------|
| `pyproject.toml` | bump 0.9.0→0.10.0 | targets 0.10.0 | reads (dependency) | — | 🟡 | See NC-01 — version sequencing conflict |
| `CHANGELOG.md` | adds `## [0.10.0]` | stacks entry above 0.10.0 | reads | — | 🟡 | Spec-mandated sequencing: #1793 first, #1838 rebases on top; implies #1838 block needs 0.11.0 |
| `src/roxabi_contracts/jobs/subjects.py` | renames return values + adds 3 helpers | — | consumes helpers (hard dep) | — | 🟢 | #1812 hard-dep on #1793; no collision if ordered correctly |
| `src/roxabi_contracts/jobs/models.py` | docstring fix line 72 | `WorkEnvelope` reparent (4 CLI models) | — | — | 🟢 | Different models; additive only; independent edits |
| `src/roxabi_contracts/cli/` (CliCmdPayload etc.) | — | reparents 4 models to WorkEnvelope | — | — | 🟢 | Solo touch |
| `tests/test_jobs_models.py` | 2 assertions updated | — | — | — | 🟢 | Solo touch |

### src/factory/ (hub codebase)

| File | #1793 | #1009+#1838 | #1812 | #1620 | Severity | Note |
|------|-------|-------------|-------|-------|----------|------|
| `src/factory/llm/cli_nats_codec.py` | — | session/op changes | — | adds `root_job_id` kwarg (line ~90) | 🟢 | Orthogonal edits on different sections |
| `src/factory/llm/llm_client.py` | — | removes `resume_and_reset` (line 160) | — | explicitly DEFERRED (no change) | 🟢 | #1620 deferred threading here; #1009 removal only |
| `src/factory/core/ports/llm_types.py` | — | — | — | adds `root_job_id` to `InboundMessage` | 🟢 | Solo touch |
| `src/factory/adapters/telegram/telegram_normalize.py` | — | — | — | mint sites lines 155+231 | 🟢 | Solo touch |
| `src/factory/adapters/discord/discord_normalize.py` | — | — | — | mint site line 120 | 🟢 | Solo touch |
| `src/factory/adapters/clipool/` | — | `clipool_worker.py` (remove resume_and_reset) | — | `pool_observer.py`, `pool_processor_streaming.py`, `pool_processor_exec.py` | 🟢 | #1009 removes dead op; #1620 adds kwarg threading — orthogonal |
| `src/factory/transport/turn_publisher.py` | subjects rename callsites (if any) | — | — | adds `root_job_id` kwarg (line ~89) | 🟢 | Subject rename only touches return-values; callers using helper functions unchanged |
| `src/factory/adapters/omp/` | — | — | new module (omp_worker.py + _rpc_bridge.py) | — | 🟢 | New directory; zero collision |
| `src/factory/adapters/cli_pool_session.py` | — | modified (session mixin) | — | — | 🟢 | Solo touch in #1009 |
| `src/factory/llm/drivers/` (simple_agent.py) | — | removes resume_and_reset handler (lines 175-192) | — | — | 🟢 | Solo touch |

### deploy/ and tools/

| File | #1793 | #1009+#1838 | #1812 | #1620 | Severity | Note |
|------|-------|-------------|-------|-------|----------|------|
| `deploy/nats/acl-matrix.json` | no touch | no touch | adds `omp-worker` identity | no touch | 🟢 | Solo touch; triggers full 4-artifact ACL fan-out |
| `tests/scripts/fixtures/v3-pre-grant-group.json` | — | — | hand-mirror of ACL diff (NOT script-generated) | — | 🟢 | Solo touch; CI-only gate (`test_v4_render_set_equals_v3_render`) |
| `tools/check_str_exc_bus_bound.sh` | — | — | extends gate to `src/factory/adapters/` | — | 🟢 | Solo touch in #1812 |
| `roxabi-plugins` (canonical copy of gate) | — | — | mirrors gate change | — | 🟢 | External repo; #1812 side-effect |

### Satellite repos (READ-ONLY — #1840 scope)

| Repo file | #1840 touch | Factory overlap | Severity |
|-----------|-------------|-----------------|----------|
| `llmCLI/nats/_generation.py` | 5 construction sites | none | 🟢 |
| `voiceCLI/synthesize_adapter.py` | TtsResponse + early-exit fix | none | 🟢 |
| `voiceCLI/transcribe_adapter.py` | two-layer extraction | none | 🟢 |
| `imageCLI/nats/adapter.py` | ImageResponse + _reply_error fan-out | none | 🟢 |
| Each repo: `.github/workflows/contracts-bump-caller.yml` | new file | none | 🟢 |

**Summary:** Zero 🔴 BLOCKER collisions. Two 🟡 MEDIUM sequencing constraints on `CHANGELOG.md` / `pyproject.toml`. All other touches are orthogonal.

---

## 2. Merge Order Recommendation

```
[Parallel group A — no dependencies between each other]
  #1620  (ingress job_id mint — pure factory, no contracts change)
  #1840  (satellite echo job_id — zero factory file contact)

[Gate: #1793 must merge before #1812 and before #1009+#1838 finalize version]
  #1793  ← MERGE FIRST in contracts chain

[Parallel group B — both depend on #1793 landing]
  #1812  (OmpWorker — hard dep on #1793 subjects; concurrent with #1009+#1838 safe after #1793)
  #1009+#1838  (CLI session lifecycle — rebases on #1793, stacks CHANGELOG entry above [0.10.0])
              NOTE: see NC-01 re version number; likely needs 0.11.0 not 0.10.0

[Gate: #1841 hard-require flip — blocked until ALL 3 #1840 satellite PRs merged + deployed]
  #1841  (job_id hard-require on WorkEnvelope — out of batch 2, held)
```

**Rationale:**
- #1793 is a prerequisite for #1812 (OmpWorker literally imports `jobs_progress`, `jobs_result` from contracts, which must return new paths first).
- #1620 has no contracts change — independent at all times.
- #1840 touches zero factory files and zero shared NATS subjects — fully parallel with all factory lanes.
- #1009+#1838 and #1812 share no files after #1793 lands (different modules entirely).
- CHANGELOG/pyproject collision between #1793 and #1009+#1838 is resolved by sequencing alone (rebase rule in spec); version number conflict still needs resolution (NC-01).

---

## 3. Semantic Coherence Checks

### Seam A: #1793 subjects rename ↔ #1812 OmpWorker wire contracts

**Status: INCONSISTENCY FOUND — NC-02**

The #1812 spec "Wire Contracts Decision" table (decision rationale section) explicitly lists:
- `factory.progress.<job_id>` — **old path** (pre-#1793)
- `factory.results.<job_id>` — **old path** (pre-#1793)

However, the #1812 implementation section uses:
- `jobs_progress(job_id)` → post-#1793 returns `factory.job.<job_id>.progress`
- `jobs_result(job_id)` → post-#1793 returns `factory.job.<job_id>.result`

These are wire-incompatible. The decision table states #1793 is a hard prerequisite and that OmpWorker "reuses existing subjects" — but post-#1793, the helpers return **new** subject strings, not the old ones cited in the decision table.

Two interpretations:
1. Decision table is stale documentation; implementation intent is correct (new paths post-#1793). ACL grants in `acl-matrix.json` must grant `factory.job.*.*` NOT `factory.results.*`/`factory.progress.*`.
2. OmpWorker was spec'd pre-#1793 decision and the decision table reflects the actual intended wire paths (old subjects), meaning the helper usage is wrong.

**Must clarify before #1812 implementation starts.** Implementer must confirm ACL grant subjects to avoid silent publish-to-wrong-subject at deploy time.

### Seam B: #1009+#1838 CLI model reparent ↔ satellite consumers

**Status: COHERENT — no action needed**

WorkEnvelope reparent adds `job_id: str | None = None` (default_factory shim from #1619). The 4 CLI models (CliCmdPayload, CliChunkEvent, CliControlCmd, CliControlAck) gain this field with a default. `ContractEnvelope.extra="ignore"` ensures pre-reparent satellites drop the new field silently. CliHeartbeat correctly stays on ContractEnvelope (INFRA classification, not a job-bearing message). Wire-safe.

`resume_and_reset` removal: #1009 removes this dead op from CliControlCmd + all handlers. Spec mandates caller verification (NC-03): `src/factory/adapters/clipool/cli_pool.py` must be audited to confirm no live callsite dispatches `resume_and_reset` before merge.

### Seam C: #1620 root_job_id threading ↔ LlmRequest/TurnWriteEvent job_id field

**Status: COHERENT — deferral boundary correctly scoped**

#1620 threads `root_job_id` through `InboundMessage` → clipool observers → `TurnPublisher` → `TurnWriteEvent.job_id` and `LlmRequest.job_id`. The LlmClient.complete()/stream() threading is explicitly DEFERRED (requires LlmProvider protocol change). The spec's SC-7 asserts `LlmRequest.parent_job_id is None` — confirming the deferred scope is intentional, not a gap.

The `root_job_id` field on `InboundMessage` is an in-process only dataclass (not serialized over NATS), so no wire-compat concern on addition.

---

## 4. Worktree Plan

All worktrees under `.claude/worktrees/`. Base: `staging` at 37a38621.

| Lane | Worktree slug | Base branch | Can start immediately? | Blocks / Blocked-by |
|------|--------------|-------------|------------------------|---------------------|
| #1793 | `1793-jobs-taxonomy` | staging | YES | Blocks #1812, #1009+#1838 final version |
| #1620 | `1620-ingress-job-id` | staging | YES | Independent |
| #1840-llmCLI | `1840-llmcli-echo-job-id` | staging (llmCLI repo) | YES | Independent |
| #1840-voiceCLI | `1840-voicecli-echo-job-id` | staging (voiceCLI repo) | YES | Independent |
| #1840-imageCLI | `1840-imagecli-echo-job-id` | staging (imageCLI repo) | YES | Independent |
| #1812 | `1812-omp-worker` | staging | NO — wait for #1793 | Hard-dep on #1793 merged contracts 0.10.0; NC-02 must resolve first |
| #1009+#1838 | `1838-cli-session-lifecycle` | staging | NO — wait for #1793 | Rebases on #1793; NC-01 must resolve first |

**Notes:**
- #1840 satellite worktrees are in separate repos; `tools/worktree-setup.sh` does not apply there — standard `git worktree add` only.
- #1812 worktree should be created post-#1793 merge to avoid rebase churn on `jobs/subjects.py`.
- #1009+#1838 worktree can be prepared pre-#1793 but MUST rebase before any `roxabi-contracts` edit.
- Pre-push gates (test_sleep, arch_snapshot, debt_expiry, secrets_drift, import_layers, volumes_table) are CI-only; run manually before push per memory `feedback-prepush-gates-skip-on-lead-worktree-push`.
- #1812 ACL fan-out: after `acl-matrix.json` edit, manually regenerate auth.conf + 706-spec + v3-current + CURRENT.generated.md AND hand-mirror diff into `tests/scripts/fixtures/v3-pre-grant-group.json` (NOT script-generated).

---

## 5. Consolidated NEEDS CLARIFICATION

| ID | Lane(s) | Blocker? | Question | Source |
|----|---------|----------|----------|--------|
| NC-01 | #1793 + #1009+#1838 | YES for #1009+#1838 impl | **Version collision:** #1793 lands at 0.10.0; #1009+#1838 spec says `version_target: "roxabi-contracts 0.10.0"` and "stacks version entry ABOVE the [0.10.0] block" — this implies a NEW version entry, meaning 0.11.0 is needed, not a second 0.10.0. Spec #1009 asserts "0.10.0 is correct regardless" but that predates the confirmed CHANGELOG sequencing. Confirm: does #1009+#1838 target 0.11.0? | #1793 spec §Versioning + #1009 spec §S3 |
| NC-02 | #1812 | YES — wire correctness | **Subject path mismatch in #1812 spec:** Wire Contracts Decision table shows old paths (`factory.results.<job_id>`, `factory.progress.<job_id>`); implementation uses post-#1793 helpers that return new paths (`factory.job.<id>.result`, `factory.job.<id>.progress`). Which paths should OmpWorker actually publish to? ACL grants in `acl-matrix.json` depend on the answer. | #1812 spec §Wire Contracts Decision vs §Subject constants |
| NC-03 | #1009+#1838 | YES — correctness | **`resume_and_reset` live callers:** Spec mandates verification of `src/factory/adapters/clipool/cli_pool.py` to confirm no live `resume_and_reset` dispatch exists before removal. Spec lists this as an NC; must be grep-confirmed before merge. | #1009 spec §NCs |
| NC-04 | #1812 | NO — risk only | **`factory.omp.heartbeat` ACL grant:** Spec NCs flag whether hub needs a subscribe grant on `factory.omp.heartbeat`. This literal string does not go through subject helpers. Confirm grant requirement before `acl-matrix.json` edit to avoid a 4-artifact fan-out redo. | #1812 spec §NCs |
| NC-05 | #1812 | NO — spec gap | **OmpProgressEvent / OmpResultEvent schema location:** Spec mentions these models but does not specify whether they live in `roxabi-contracts` (new domain submodule) or in `src/factory/adapters/omp/`. Confirm before implementation to avoid contracts-vs-factory boundary violation. | #1812 spec §NCs |

---

## 6. Go / No-Go Per Lane

| Lane | Go/No-Go | Condition |
|------|----------|-----------|
| **#1793** jobs taxonomy | **GO** | No open questions; zero callers outside contracts confirmed in spec; wire-safe rename. Start immediately. |
| **#1620** ingress job_id mint | **GO** | No contracts change; pure in-factory threading; no NCs. Start immediately. |
| **#1840** satellite echo job_id (all 3) | **GO** | Spec explicitly confirms zero factory file contact, zero shared NATS subjects. All 3 can run in parallel. Conditional kwarg exclusion pattern is well-specified. |
| **#1009+#1838** CLI session lifecycle | **NO-GO** | Blocked on: (1) #1793 merge (rebase dependency), (2) NC-01 version number resolution, (3) NC-03 `resume_and_reset` grep confirmation. |
| **#1812** OmpWorker | **NO-GO** | Blocked on: (1) #1793 merge (hard dep on new subject helpers), (2) NC-02 wire path resolution (fundamental correctness), (3) NC-04+NC-05 ACL/schema placement (risk mitigation). |
| **#1841** hard-require flip | **NO-GO** | Out of batch 2; blocked until all 3 #1840 satellite PRs merged AND deployed to production. |

**Immediate parallel starts:** #1793, #1620, #1840×3 (5 independent worktrees).  
**Gated:** #1812 and #1009+#1838 wait for #1793 merge + NC resolution.

---

## Lead arbitrage record (2026-06-11, post-reconcile)

| NC | Resolution | Evidence |
|---|---|---|
| NC-01 (cli version 0.10 vs 0.11) | Rule, not number: next minor above staging HEAD at rebase time (after #1793) | spec 1009 frontmatter + S3 updated |
| NC-02 (1812 old subjects) | Post-#1793 paths canonical: `factory.job.<id>.{progress,result}`; spec 1812 rewritten (10 edits); `opened/closed` deferred to #1796/#1798 consumers | revise agent aca6c05 applied |
| NC-03 (resume_and_reset callers) | Op is LIVE — `pool.resume_session()` called from path_validation.py:88,140 / pool_processor.py:51 / session_commands.py:110 → resume_fn → LlmClient → wire op → functional worker branch. Spec's "dead op / never dispatched / worker rejects" premise REFUTED. `CliCmdPayload.resume_session_id` (cli/models.py:28) is DORMANT (no producer sets it, worker never reads it) → #1009 item 2 = MIGRATION to embedded resume, not deletion. Spec 1009 Expected Behavior/S2/SCs rewritten accordingly | grep evidence in session; spec section "Live-op retirement" |
| NC-04 (omp heartbeat subject) | `factory.omp.heartbeat` CONFIRMED — matches convention (factory.{image,llm,clipool}.heartbeat, acl-matrix.json:63-68); hub subscribe grant mirrors clipool pattern | spec 1812 updated |
| NC-05 (Omp*Event models) | REJECTED new per-runtime models (N×M drift). OmpWorker publishes existing `JobProgress`/`JobResult` (WorkEnvelope) extended ADDITIVELY (optional fields) if needed | spec 1812 updated |

Additional (replacement architect review of 1009 — original died of context thrash ×2):
- Deprecation policy honored: op Literal keeps `resume_and_reset` (deprecated) one minor; worker branch retained functional; `cli_session_id` takes `AliasChoices("cli_session_id","session_id")`; mechanical removal = ship-time follow-up sibling under #1044 blocked-by #1009.
- llm_client.py added to S2 scope (CliControlCmd construction at ~:183 + payload builds :89/:115).
- S3 unification: LlmClient = single session-mapping owner (NATS path); simple_agent.py resume chain KEPT (live).
- SC-3/SC-4 semantics reversed; SC-18 (embedded e2e) + SC-19 (producer purge) added → 19 SCs.

Merge order unchanged: 1793 ∥ 1620 ∥ 1840×3 immediately; 1009+1838 and 1812 branch from post-#1793 staging.
