# roxabi-factory — Full Audit Report (consolidated, max detail)

> **Single-file consolidation** of the 2026-06-30 multi-agent audit: executive reduce-synthesis + all 8 domain shards verbatim + the Claude lead's independent verification. Nothing here was filed as a GitHub issue — mutations go through `roxabi-issues:issue-triage`.

| | |
|---|---|
| **Audit date** | 2026-06-30 (consolidated 2026-07-01) |
| **Repo / branch** | `roxabi-factory` @ `staging` (`42b50ee8`, after ff-pull of PR #2070) |
| **Week window** | 392 commits since 2026-06-23 (base `2714b361` → `staging`), ~1151 files, +40k/-7k |
| **Scope** | whole-repo + last-week delta + cross-repo deploy (`~/projects` cluster installer) |
| **Method** | ground-truth gates → 26 read-only finders (8 domains × repo/week/cross-repo) → adversarial per-finding verify (crit/high) → map-reduce synthesis |
| **Discovery engine** | `ccc` (cocoindex semantic search), daemon-fresh index (25 371 chunks / 2 451 files) + grep + the 16 repo quality gates |
| **Result** | **77 deduped findings — 5 P0 / 13 P1 / 37 P2 / 22 P3 · debt 38/100 · 16/16 gates green** |
| **Report artifacts** | this file · `AUDIT-SUMMARY.md` · `by-domain/*.md` (8) |
| **Workflow script** | `artifacts/plans/full-audit.wf.js` (re-runnable) |

## Provenance — produced in two passes

This audit ran twice. **Run-1** (69 agents) lost 6 finders: both `axial-drift` finders and 4 of 5 `security` finders used custom agentTypes (`dev-core:axial-adr-review`, `dev-core:security-auditor`) that emit prose and did **not** reliably call the structured-output tool → those domains came back near-empty (axial-drift = 0, security = 2). **Run-2** (resume, 83 agents) switched those 6 finders to the `Explore` agentType (100 % reliable with schema) and regenerated the synthesis — the numbers above are run-2. See **Appendix A** for the full coverage note. The bug was in the audit harness, not the codebase.

## Table of contents

- **Part 0 — Independent verification (Claude lead)** — P0 spot-checks, with caveats the verify agents missed
- **Part I — Executive synthesis (reduce step)** — full `AUDIT-SUMMARY.md`: debt score, severity totals, cross-domain merges, thematic clusters, P0/P1 tables, axial-drift table, SSoT/duplication table, deploy section, metrics dashboard, top-10 quick wins
- **Part II — Full domain detail (all findings verbatim)**
  - architecture · axial-drift · security · ssot · deploy · week-subsystem · contracts · error-async
- **Appendix A** — Coverage note (run-1 gap → run-2 fix)
- **Appendix B** — Run metadata (agents, tokens, finder matrix)
- **Appendix C** — Reproduce / next actions

---

# Part 0 — Independent verification (Claude lead)

The verify stage labels many findings `CONFIRMED`, but those verdicts come from agents. Below is the lead's **own** read-only verification of the 5 P0s (opening the cited `file:line` by hand). **Key takeaway: 2 of the 5 P0s are slam-dunk-confirmed; 3 carry caveats and must not be acted on as certainties without the noted runtime/cross-repo confirmation.**

| # | P0 | File:line | Lead verdict | Caveat / what's still needed |
|---|---|---|---|---|
| P0-1 | Dashboard admin/agents/jobs BFF — **zero operator auth** (privesc via `grant()`, persona hijack, identity disclosure) | `routes/bff_admin.py:24`, `bff_agents.py:22`, mounted `app.py:26` | ✅ **CONFIRMED (hand-verified)** | None. `connectors.py` wires `Depends(require_operator)` on all routes; the new routers wire none; routes are mutating, mounted, and reach `grant_store.grant()` (`admin_rpc.py:116`). Independently hit by 5/5 security finders + ssot + 2 peer sessions. |
| P0-2 | `/api/bff/jobs/steer` — no auth **and no ownership check** → IDOR text-injection into any live session | `bootstrap/factory/dashboard_jobs_rpc.py:100` (route `bff.py:136`) | ✅ **CONFIRMED (located run-2)** | My run-1 grep missed the file (it's under `bootstrap/factory/`, not `dashboard/`); run-2 pinned it. `handle_jobs_steer` publishes `req.text` to `jobs_steer(job_id)` with only a subject-char sanitizer, no principal/ownership check. |
| P0-3 | telegram/discord ACL missing `$JS.API.STREAM.NAMES` (+`CONSUMER.INFO`) — same gap crash-looped the dashboard the same day, not backported | `deploy/nats/acl-matrix.json:94` | ⚠️ **Asymmetry CONFIRMED, reachability UNVERIFIED** | web-adapter **has** `STREAM.NAMES` (re-added in the 2026-06-30 hotfix `95b566bc`); telegram/discord had it **removed** (#1727, kv.watch→kv.get migration) and never re-added. Real only if their kv.watch fallback path is still reachable in the current roxabi-nats — verify against the SDK fallback before treating as live. |
| P0-4 | OMP V2 `set_system_prompt` — method does not exist on real `omp_rpc.RpcClient` → persona/soul silently never applied | `adapters/omp/omp_pool.py:64` | ⚠️ **Silent-no-op CONFIRMED, method-absence UNVERIFIED** | Code is `getattr(client, "set_system_prompt", None)` → a **guarded no-op, not a crash**. Tests pass because `MagicMock` answers any attribute. `omp_rpc` is **not importable in this venv** (it lives in the omp-base image) → cannot confirm the method is absent locally. Confirm at M₁ runtime (`podman run … omp_rpc`), per the "verify container boot on M₁" discipline. |
| P0-5 | `cluster_plan.py` role-rename desyncs from `quadlet.toml host_roles`; unattended converge auto-`--prune`s live prod units | `~/projects/lib/cluster_plan.py:45` (projects-meta) | ⚠️ **Plausible, NOT spot-checked** | Cross-repo (`~/projects`, not this repo). Matches the documented `project-hosts-role-rename-fanout` memory + escalated by converge auto-prune. Read `cluster_plan.py` + `deploy.sh --prune` path before acting. |
| P1 | Unauth admin PATCH → platform-identity **rebind** onto arbitrary `user_id` (account takeover / chat impersonation) | `admin_rpc.py:241` → `user_store_profile.py:131` | ✅ **CONFIRMED (hand-verified)** | `set_platform_identity` exists; route unauth (folds into P0-1). Distinct logic bug: fixing auth alone doesn't add the ownership check. |
| P2 | LogQL injection — `container` query param interpolated into stream selector unescaped | `dashboard/ops_proxy.py:64` | ⚠️ **Plausible** | f-string `systemd_unit="{unit}"`; `_systemd_unit_for_container` only `.strip()`s + one `raise` — confirm the validation lines don't block `"`/metachars. |

**Actionable read:** the **dashboard cluster (P0-1 + P0-2 + P1 rebind)** is the one to fix first — same PR (#2070), same day, hand-confirmed, Tailnet-reachable, possibly already on the staging→ghcr image path. P0-3/P0-4/P0-5 are strong leads needing one confirmation step each.

---

# Part I — Executive synthesis (reduce step)

## roxabi-factory — Full Audit Summary (2026-06-30)

Reduce pass over **8 domain shards** (architecture, axial-drift, security, ssot, deploy, week-subsystem, contracts, error-async). Week-over-week window: 392 commits since 2026-06-23 (base `2714b361` → `staging`), dominated by the operator-console redesign (PR #2070, `feat/dashboard-admin-users-agents`) and fleet container observability post-MVP work (`feat/fleet-container-obs-post-mvp`), merged together in `00406cdd`, plus the ingress-connector-tenant-contract thread (ADR-096).

**All 16 quality gates pass clean** (ccc-index, importlinter 15/15, doc_drift, doc_semantic_drift, secrets_drift, secrets_source, quadlet_manifest_install, quadlet_component_source, str_exc_bus_bound, hardcoded_constants, file_length, folder_size, test_sleep, omp_pin_lockstep, architecture_snapshot, volumes_table). Green-CI gives **zero protection** against 4 of this audit's 5 P0 findings — the dashboard-BFF auth bypass, the jobs/steer IDOR, the unbackported NATS ACL grant, and the OMP `set_system_prompt` API mismatch — none of these are gate-checkable today.

> **Data provenance note**: `by-domain/ssot.md` and `by-domain/deploy.md` each report that a *different, larger* prior finding set previously existed at their file paths (e.g. ssot: 31 raw/29 deduped findings incl. `quadlet_component_source gate never wired`, `ingress.toml never provisioned`; deploy: `converge.sh:64 NO_RESTART=1` permanent daemon-reload skip, `cluster_plan.py:75-82 managed_repos allowlist silent-drop`). Those findings are **not REFUTED**, just absent from the JSON input handed to this reduce pass, and were overwritten per each domain synthesis's explicit instructions. They should be reconciled in a follow-up pass rather than treated as resolved.

### Executive summary

The week's two flagship features (dashboard admin/agents + fleet observability) introduced the audit's most severe finding: dashboard BFF admin/agent/job-control routes ship with **zero operator authentication**, independently confirmed by both the **security** domain (5/5 finders converged, 6 raw findings merged into one) and the **ssot** domain (SSoT-vocabulary check against `AgentGrantStore`/ADR-090) — same files (`bff_admin.py`, `bff_agents.py`), same root cause, same week. This is a Tailnet-reachable, net-new production surface with real mutation power (agent-grant writes, system-prompt overwrite, cross-session text injection, identity rebind) and is the unambiguous top fix priority.

Four more P0s round out the critical tier: an independent IDOR on `/api/bff/jobs/steer` (security), an unattended `cluster_plan.py --prune` role-drift hazard that can silently delete live production Quadlet units on a `hosts.toml` role rename (deploy), a never-backported NATS ACL grant gap that already crash-looped the dashboard once this week (deploy), and a silently-broken OMP persona/soul system-prompt apply — the just-shipped `omp_pool.py` calls `set_system_prompt`, a method that does not exist on the real `omp_rpc.RpcClient`, undetected because the test suite mocks an attribute the real client never had (week-subsystem). The OMP bug is the clearest instance of this audit's recurring "registered/mocked ≠ reachable/conformant" failure mode (matches operator memory `project-backend-reachable-and-protocol-conformance.md`).

Debt is heavily concentrated in **ssot** (22 findings, subscore 40/100 — doc/topology drift, dead-but-declared SSoT subjects, duplicated KV-bucket idioms) and **security** (13 findings, subscore 15/100 — the lowest subscore of any domain, reflecting one severe, fresh, multi-confirmed access-control failure rather than diffuse debt). **contracts** (90/100) and **axial-drift** (76/100) are the healthiest domains this cycle.

### Global Technical Debt Score: **38 / 100** (100 = pristine)

Methodology: each domain's `debt_subscore` weighted by its severity-weighted finding volume (weights: critical=25, high=15, medium=8, low=3), i.e. `Σ(subscore_i × weight_i) / Σ(weight_i)`. A naive finding-count-weighted average gives 35/100 (closer due to ssot/security volume); an unweighted mean of the 8 raw subscores gives 51/100 — the severity-weighted score is used as the headline because it does not let high finding-count, low-severity domains mask a concentrated, high-severity domain (security, 13 findings but subscore 15).

| Domain | Debt subscore | Findings (raw) | Severity-weighted volume |
|---|---:|---:|---:|
| architecture | 60 | 9 | 64 |
| axial-drift | 76 | 4 | 22 |
| security | 15 | 13 | 137 |
| ssot | 40 | 22 | 189 |
| deploy | 33 | 7 | 80 |
| week-subsystem | 35 | 17 | 173 |
| contracts | 90 | 2 | 6 |
| error-async | 58 | 5 | 44 |
| **Weighted average** | **37.6 → 38** | **79** | **715** |

### Severity totals

| | Critical | High | Medium | Low | Total |
|---|---:|---:|---:|---:|---:|
| Raw (sum of all 8 domain shards) | 6 | 13 | 38 | 22 | 79 |
| **Deduped (this reduce pass)** | **5** | **13** | **37** | **22** | **77** |

Dedup removed 1 critical (security's dashboard-BFF-auth finding ≡ ssot's `ssot-ccc-sweep-001`, same root cause) and 1 medium (architecture's socialmedia `str(exc)` leak symptom ≡ security's `check_str_exc_bus_bound.sh` gate-blind-spot root cause — same gate, same verified false-negative). No high/low cross-domain exact duplicates found. See merge table below.

### Cross-domain duplicate merges

| Merged finding | Domains (independent confirmation) | Files | Resolution |
|---|---|---|---|
| **P0 — Dashboard admin/agent/job-control BFF routes have zero authentication** | security (critical, merges 6 raw findings, 5/5 finders) + ssot (critical, `ssot-ccc-sweep-001`) | `src/factory/dashboard/routes/bff_admin.py`, `bff_agents.py` | One P0 issue. Two domains reached the same conclusion via different methods (security: route-by-route auth-decorator diff vs. `routes/connectors.py`; ssot: SSoT-vocabulary check against `AgentGrantStore`/ADR-090) — confidence raised to maximum. |
| **P2 — `str_exc_bus_bound` gate has blind spots that let raw exception text reach bus-bound error fields** | architecture (medium, symptom: `socialmedia/adapter.py:295` leak) + security (medium, root cause: `tools/check_str_exc_bus_bound.sh:12,27` regex/scan-root gap) | `src/factory/adapters/socialmedia/adapter.py:295`, `tools/check_str_exc_bus_bound.sh:12,27` | One P2 issue covering both the gate fix and its two known false-negative sites (socialmedia adapter, confirmed by architecture; dashboard admin RPCs, confirmed by security). |

#### Checked-but-not-duplicate (overlapping files, distinct defects — kept separate)

- **Shared hotspot, not a dup**: error-async's `err-async-001`/`err-async-003` (18 `except Exception` sites missing AGENTS.md-mandated inline justification, and a wrong-exception-taxonomy 500-vs-503 bug) sit in the **same files** (`bff.py`, `bff_admin.py`, `bff_agents.py`) as the P0 auth-bypass merge above, but are different defect classes (missing-comment / wrong-error-mapping vs. missing-authorization). Fix in one coordinated pass on these files, but tracked as separate issues.
- ssot's `docs/architecture/security-routing.md:87` (ADR-090 described as future work) was checked against security's 2 findings (dashboard-BFF auth, LogQL injection) — does **not** cover ADR-090 staleness. Kept standalone (P1).
- security's `sec-authz-002` (ADR-090 admin-bypass not implemented — availability bug) and ssot's `security-routing.md:87` (ADR-090 doc says "planned" when it's shipped) both cite ADR-090 but describe different specific defects (implementation gap vs. doc staleness) — kept separate, flagged below as a thematic cluster.

### Thematic clusters (not merged — distinct root causes, same subsystem)

1. **NATS-KV id-sanitization fragility** — architecture's `kv_safe_part` non-injective collision (`_kv_keys.py:13`, **P1, CONFIRMED** — distinct `pool_id`s collide on the same sanitized KV key) sits in the same surface as ssot's duplicated create-or-open KV-bucket idiom (5+ modules, one already drifted, `ssot-ccc-sweep-003`) and architecture's duplicated NATS-identifier regex with no parity test (`roxabi_satellite/tokens.py:11`). This is the same surface that caused a real prior outage (operator memory `project-natskv-poolid-colon-key-outage.md`, colon-sanitization bug). Recommend one consolidated NATS-KV-id-hygiene workstream.
2. **`hosts.toml` role-SSoT gap** — deploy's P0 (`cluster_plan.py:45`, unattended `--prune` deletes live prod units on role rename) and deploy's own self-flagged dup (`deploy/lib/quadlet-units.sh:12`, role-blind client-restart fan-out) sit alongside ssot's `deploy/quadlet.toml:167` (`network.roxabi` `host_roles` scoped to `factory-hub` only, breaks fresh M2 provisioning). Three distinct bugs, one root deficiency: no single canonical role-validation SSoT across `hosts.toml` and every managed repo's `quadlet.toml`. Matches operator memory `project-hosts-role-rename-fanout.md` exactly. Recommend a shared `~/projects/lib/` role-validation helper, not three separate point-fixes.
3. **OMP/omp-rpc composition + protocol fragility** — architecture found `unified.py` ("factory start") registers an omp-rpc backend it can never construct in-process; week-subsystem separately found that where OMP **is** constructed (`omp_pool.py`), it calls `set_system_prompt`, a method the real `omp_rpc.RpcClient` does not have (only the test's bare `MagicMock()` has it) — this is this audit's sole P0 in week-subsystem. Two distinct bugs, same subsystem, same "registered/mocked ≠ reachable/conformant" pattern as operator memory `project-backend-reachable-and-protocol-conformance.md`. Recommend one OMP composition-root + protocol-conformance audit.
4. **ADR-090 fidelity drift** — referenced by 3 distinct findings across 2 domains: security's P0-adjacent admin-bypass-not-implemented (availability bug), ssot's doc-staleness (`security-routing.md:87` says "planned", it's shipped), and security's blast-radius note that the dashboard P0 is the same surface ADR-090 was meant to gate. Not merged (different specific defects) but should be fixed as one ADR-090 hardening pass.
5. **`str_exc_bus_bound` gate blind spot** — see merge table above (architecture + security).
6. **roxabi-obs CI onboarding gap** — week-subsystem-internal pair: `pyproject.toml:138` (pyright include omits `packages/roxabi-obs/src`) + `pyproject.toml:109` (pytest testpaths lists `roxabi-obs/tests` but no CI job ever passes that path) — same root cause (new package never added to CI directory-scope allowlists), one fix closes both P1s.
7. **Silent-failure cluster (zero error signal on brand-new code)** — week-subsystem alone: OMP persona apply silently no-ops (P0), soul cache-miss fallback can transiently blank a live system prompt (P2), ingress connector-restart silently re-seeds a deleted webhook (P2). All three are zero-error-signal failures in code shipped this week. Recommend a standing silent-failure audit practice, not a one-off fix.
8. **Dashboard admin/agent surface concentration** — the persona-soul-blobstore epic (PR #2068, merged immediately pre-audit) is the common ancestor of `omp_pool.py:64` (P0), `dashboard_agents_rpc.py:85` (soul-get missing blob-fallback, P2), and `soul_ops.py:131` (cache/store race, P2) — concentrated-risk feature surface; recommend a dedicated hardening follow-up rather than scattered point-fixes.

### Axial-drift summary table

`axial-drift` is this cycle's healthiest domain by volume (4 findings, subscore 76/100) but flags one pattern worth tracking against ADR-073 ("Axial Stage-of-Pipeline Decomposition" — 3+ sibling copies of cross-cutting logic is the actionable threshold):

| Severity | File:Line | Finding | Status |
|---|---|---|---|
| medium | `src/factory/llm/claude_job_codec.py:24` | `WorkerError` validation helper triplicated across claude/omp/cli_pool codec modules | CONFIRMED (downgraded high→medium: behaviorally identical today, prospective risk only) |
| medium | `apps/dashboard/src/components/agents/AgentsListPanel.tsx:287` | Telegram/Discord/Email presence rendering hardcoded across 5 dashboard sites instead of one platform list | UNVERIFIED |
| low | `src/factory/bootstrap/factory/dashboard/admin_rpc.py:45` | `_agent_store(hub)` helper byte-identically duplicated across two dashboard RPC modules (PR #2070's file-length split left it un-consolidated) | UNVERIFIED |
| low | `src/factory/ingress/` | New ingress subsystem (ADR-096) has no `AGENTS.md` documenting the stage-axis Connector contract | UNVERIFIED |

Note: finding #1 (codec triplication) is the clearest textbook ADR-073 hit this cycle. Findings #2+#3 are both symptoms of the same PR #2070 file-length split skipping shared-helper extraction — flagged as one systemic root cause if other domains hit `admin_rpc.py`/`dashboard_agents_rpc.py` again.

### SSoT / duplication table

Beyond the two cross-domain merges above, `ssot.md` independently surfaces its own internal duplication clusters (not cross-domain, kept within ssot's own count of 22):

| Cluster | Files | Severity | Note |
|---|---|---|---|
| KV bucket create-or-open idiom (3-way merge) | `infrastructure/kv`, `outbound_audio`, `stores/jobs`, `stores/kv`, `blobstore` (5 modules) + `roxabi-nats` | low (downgraded from high — near-zero production blast radius) | `ssot-ccc-sweep-003` ⊃ `ssot-stores-1` ⊃ `ssot-stores-3`, same try-create→`BadRequestError`/`BucketNotFoundError`→bind logic |
| Container/component enumeration drift | `deploy/install.sh:403`, `docs/architecture/deployment.md:7`, `AGENTS.md:86` | medium/high | 3 independent hand-maintained lists, none derived from `docs/architecture/CURRENT.generated.md` Process Topology |
| Dashboard operator-auth pair | `bff_admin.py`/`bff_agents.py` (P0, merged above) + `hmac.compare_digest` reimplemented 6x with no shared `verify_shared_secret` primitive | critical + medium | ssot's own evidence: the duplication pattern is what let the P0 auth gap ship unnoticed |
| Cross-repo deploy/install convention divergence | `secrets-policy.toml` (factory-only) + `install.sh` diverged 4 ways across factory/voiceCLI/llmCLI/imageCLI | medium | both stem from absence of a shared `~/projects/lib/install-common.sh` |

### Deploy section

7 findings (3 finders: deploy-factory, deploy-cluster, deploy-acl-pipeline), subscore 33/100. Two P0s:

- `lib/cluster_plan.py:45` — `hosts.toml` role renames desync silently from per-repo `quadlet.toml` `host_roles`; an unattended `--prune` run can delete live production units. **CONFIRMED.** Part of thematic cluster #2 above (`hosts.toml` role-SSoT gap).
- `deploy/nats/acl-matrix.json:94` — telegram/discord-adapter missing `$JS.API.STREAM.NAMES` + `CONSUMER.INFO` grants for `wait_for_hub()`'s `kv.watch` fallback. **CONFIRMED** — the *same bug class* already crash-looped the dashboard this same week and was fixed there but never backported to the adapters.

Three mediums round out the domain: S12 (`RestartForceExitStatus=`) carve-out missing from all 22 Quadlet units (unlike voiceCLI/llmCLI siblings); `quadlet-units.sh` bypassing `cluster_plan.py`'s host-role SSoT (self-flagged dup of the P0, kept separate — different failure mechanism); and the hand-mirrored ACL parity fixture (`v3-pre-grant-group.json`) with zero local pre-push enforcement (only full CI catches drift — matches operator memory `project-acl-identity-regen-fanout.md`). Two lows: `make quadlet-install` redundantly re-installs the static unit fleet (severity downgraded high→low on verify — confirmed idempotent, just wasteful); `factory-langfuse-web`/`-worker` `RestartSec=15` undocumented deviation from the S12 standard of 10.

### P0 — Critical (5)

| # | Finding | Domain(s) | File:Line |
|---|---|---|---|
| P0-1 | Dashboard admin/agent/job-control BFF routes have zero operator authentication — self-service agent-grant escalation, persona/system-prompt hijack, identity disclosure | security + ssot (merged) | `src/factory/dashboard/routes/bff_admin.py:24-64`, `bff_agents.py:22-95` |
| P0-2 | `/api/bff/jobs/steer` has no auth and no job-ownership check — IDOR text-injection into any live agent session | security | `src/factory/bootstrap/factory/dashboard_jobs_rpc.py:100` |
| P0-3 | `hosts.toml` role renames desync silently from per-repo `quadlet.toml` `host_roles`; unattended `--prune` deletes live production units | deploy | `lib/cluster_plan.py:45` |
| P0-4 | telegram/discord-adapter missing `$JS.API.STREAM.NAMES` + `CONSUMER.INFO` grants for `wait_for_hub()` `kv.watch` fallback — same bug crash-looped dashboard same day, never backported | deploy | `deploy/nats/acl-matrix.json:94` |
| P0-5 | OMP V2 calls nonexistent `set_system_prompt` on the real `omp_rpc.RpcClient` — persona/soul silently never applied to any OMP-backed agent | week-subsystem | `src/factory/adapters/omp/omp_pool.py:64` |

Full detail: [`by-domain/security.md`](by-domain/security.md), [`by-domain/ssot.md`](by-domain/ssot.md), [`by-domain/deploy.md`](by-domain/deploy.md), [`by-domain/week-subsystem.md`](by-domain/week-subsystem.md)

### P1 — High (13)

| # | Finding | Domain | File:Line |
|---|---|---|---|
| P1-1 | `kv_safe_part` is non-injective — distinct `RoutingKey` `pool_id`s collide on the same sanitized NATS-KV key | architecture | `src/factory/infrastructure/stores/kv/_kv_keys.py:13` |
| P1-2 | Unauthenticated admin-user PATCH allows platform-identity rebind onto an arbitrary existing `user_id` — account takeover / grant inheritance | security | `src/factory/bootstrap/factory/dashboard/admin_rpc.py:241` |
| P1-3 | Soul secret-lint is cosmetic-only — Save button never checks the warning, zero server-side enforcement, secrets become permanent system-prompt fragments | security | `apps/dashboard/src/lib/soul-secret-lint.ts:1` |
| P1-4 | `deployment.md` describes a 9-container topology; actual prod is 20 Quadlet units / 22 processes | ssot | `docs/architecture/deployment.md:7` |
| P1-5 | `security-routing.md` describes the shipped `AuthorizeAgentMiddleware` (ADR-090) as merely planned/future work | ssot | `docs/architecture/security-routing.md:87` |
| P1-6 | `network.roxabi` `host_roles` scoped to `factory-hub` only — breaks fresh M2 (llm-worker/image-worker) provisioning | ssot | `deploy/quadlet.toml:167` |
| P1-7 | `FleetReporter` report-construction sits outside the crash guard — an uncaught exception now crash-loops the entire hub via `watchdog()` | week-subsystem | `packages/roxabi-obs/src/roxabi_obs/reporter.py:82` |
| P1-8 | `roxabi-obs` (+`roxabi-satellite`) excluded from pyright include — CI typecheck blind spot hiding a real `reportArgumentType` error | week-subsystem | `pyproject.toml:138` |
| P1-9 | `packages/roxabi-obs/tests` listed in pytest `testpaths` but no CI job ever passes that path — shutdown-fix regression test never runs | week-subsystem | `pyproject.toml:109` |
| P1-10 | `rows[0]` picked from full roster instead of selected agent — wrong online/harness badge on multi-agent rosters | week-subsystem | `apps/dashboard/src/hooks/useAgentStatus.ts:21` |
| P1-11 | Bulk agents-status RPC defaults every non-targeted agent to `harness=claude-cli` — false fleet-wide offline for omp-rpc-only deployments | week-subsystem | `src/factory/bootstrap/factory/dashboard_rpc.py:229` |
| P1-12 | Dashboard BFF error-mapping never catches NATS RPC timeout/no-responders — hub-unavailable leaks as raw 500 instead of graceful 503 | error-async | `src/factory/dashboard/routes/bff_common.py:32` |
| P1-13 | Hub's single asyncio event loop blocks on synchronous file I/O on every dashboard fleet-status poll | error-async | `src/factory/nats/fleet_digest.py:54` |

Full detail: [`by-domain/architecture.md`](by-domain/architecture.md), [`by-domain/security.md`](by-domain/security.md), [`by-domain/ssot.md`](by-domain/ssot.md), [`by-domain/week-subsystem.md`](by-domain/week-subsystem.md), [`by-domain/error-async.md`](by-domain/error-async.md)

### P2 — Medium (37, condensed)

| Domain | Medium count | Representative findings | Detail |
|---|---:|---|---|
| ssot | 13 | dead SQLite `MessageIndex` store dup, `claude-md-registry.md` missing 3 AGENTS.md files, `install.sh:403` stale component enumeration, `quadlet.toml` UID hand-duplicated 31x, `check_grants.py` third hand-authored ACL copy | [`by-domain/ssot.md`](by-domain/ssot.md) |
| security | 6 (1 merged into P2 dedup above → 5 remain net-new here) | ADR-090 admin-bypass not implemented, `str_exc_bus_bound` gate blind spots (merged), `factory-cloudflared.container` missing `ReadOnly=true`/pinned tag, NATS ACL wildcard spans admin-tier subjects, LogQL injection via unsanitized container param | [`by-domain/security.md`](by-domain/security.md) |
| week-subsystem | 8 | ingress restart silently re-seeds deleted webhook, soul-get RPC no blob-fallback, `deploy/AGENTS.md` exposure-tiers table omits ingress/cloudflared, `FleetStore._rejected_ids` unbounded growth, `soul_ops.py` cache/store race, `type: ignore` masks invalid harness param (502 not 422), `qg.conf` scope misses `apps/dashboard/src` | [`by-domain/week-subsystem.md`](by-domain/week-subsystem.md) |
| architecture | 5 (1 merged into P2 dedup above → 4 remain net-new here) | core domain imports sqlite3/aiosqlite directly, two-way `TYPE_CHECKING` import cycle core/ports↔core/agent, `unified.py` registers unconstructed omp-rpc backend, `nats_connect()`-or-exit duplicated across 5 standalone roots | [`by-domain/architecture.md`](by-domain/architecture.md) |
| axial-drift | 2 | codec validation triplication, dashboard platform-presence hardcoded 5x | [`by-domain/axial-drift.md`](by-domain/axial-drift.md) |
| deploy | 3 | S12 RestartForceExitStatus= missing from 22 units, role-blind `quadlet-units.sh` client-restart fan-out, hand-mirrored ACL fixture with no local enforcement | [`by-domain/deploy.md`](by-domain/deploy.md) |
| error-async | 1 | `except Exception` in PR #2070 dashboard-admin BFF split omits AGENTS.md-mandated inline justification (18 sites) | [`by-domain/error-async.md`](by-domain/error-async.md) |
| contracts | 0 | — | [`by-domain/contracts.md`](by-domain/contracts.md) |

### P3 — Low (22, condensed)

| Domain | Low count | Representative findings | Detail |
|---|---:|---|---|
| ssot | 5 | KV-bucket create-or-open idiom reimplemented 5x (downgraded high→low on blast-radius verify) | [`by-domain/ssot.md`](by-domain/ssot.md) |
| security | 3 | stale GuardChain architecture diagram (confirmed doc-only, not a live bug) | [`by-domain/security.md`](by-domain/security.md) |
| architecture | 3 | TTS dispatch split across confusingly-named file pair, stale `inbound/AGENTS.md` importlinter exemption note, duplicated NATS-id regex with no parity test | [`by-domain/architecture.md`](by-domain/architecture.md) |
| week-subsystem | 3 | turn-writer bootstrap init outside try/finally, `container_report` ACL grants with no in-repo call site, `OpsPage.tsx` hardcoded French labels bypassing i18n | [`by-domain/week-subsystem.md`](by-domain/week-subsystem.md) |
| axial-drift | 2 | `_agent_store(hub)` byte-identical dup, new ingress subsystem missing `AGENTS.md` | [`by-domain/axial-drift.md`](by-domain/axial-drift.md) |
| contracts | 2 | CHANGELOG undocumented across 2 minor bumps, `bot_roster` `extra="forbid"` undocumented as intentional | [`by-domain/contracts.md`](by-domain/contracts.md) |
| deploy | 2 | `make quadlet-install` redundant re-install (downgraded high→low on verify), `factory-langfuse-*` `RestartSec=15` undocumented deviation | [`by-domain/deploy.md`](by-domain/deploy.md) |
| error-async | 2 | fire-and-forget `asyncio.create_task` with no held reference, duplicate KV create-or-open helpers diverge on error handling | [`by-domain/error-async.md`](by-domain/error-async.md) |

### Metrics dashboard — per-domain issues × severity

| Domain | Critical | High | Medium | Low | Total | Subscore |
|---|---:|---:|---:|---:|---:|---:|
| architecture | 0 | 1 | 5 | 3 | 9 | 60 |
| axial-drift | 0 | 0 | 2 | 2 | 4 | 76 |
| security | 2 | 2 | 6 | 3 | 13 | 15 |
| ssot | 1 | 3 | 13 | 5 | 22 | 40 |
| deploy | 2 | 0 | 3 | 2 | 7 | 33 |
| week-subsystem | 1 | 5 | 8 | 3 | 17 | 35 |
| contracts | 0 | 0 | 0 | 2 | 2 | 90 |
| error-async | 0 | 2 | 1 | 2 | 5 | 58 |
| **Raw total** | **6** | **13** | **38** | **22** | **79** | — |
| **Deduped total** | **5** | **13** | **37** | **22** | **77** | **38 (weighted)** |

### Triage table — proposed GitHub issues

Proposals only — no issues were created. Mutations go through `roxabi-issues:issue-triage` per `~/projects/ssot/operator.ssot.md`.

| Priority | Suggested issue title | Owning domain | File:Line |
|---|---|---|---|
| P0 | `fix(dashboard): require operator auth on admin/agent BFF routes (bff_admin.py, bff_agents.py)` | security (+ssot) | `src/factory/dashboard/routes/bff_admin.py:24` |
| P0 | `fix(dashboard): authenticate + check job ownership on /api/bff/jobs/steer (IDOR)` | security | `src/factory/bootstrap/factory/dashboard_jobs_rpc.py:100` |
| P0 | `fix(deploy): make cluster_plan.py --prune role-validation hosts.toml-aware before deleting live units` | deploy | `lib/cluster_plan.py:45` |
| P0 | `fix(nats): backport $JS.API.STREAM.NAMES + CONSUMER.INFO grants to telegram/discord-adapter ACL` | deploy | `deploy/nats/acl-matrix.json:94` |
| P0 | `fix(omp): omp_pool.py calls nonexistent RpcClient.set_system_prompt — persona/soul never applied` | week-subsystem | `src/factory/adapters/omp/omp_pool.py:64` |
| P1 | `fix(nats-kv): kv_safe_part collision — distinct pool_ids map to the same sanitized KV key` | architecture | `src/factory/infrastructure/stores/kv/_kv_keys.py:13` |
| P1 | `fix(dashboard): admin_rpc.py PATCH allows identity rebind onto arbitrary user_id` | security | `src/factory/bootstrap/factory/dashboard/admin_rpc.py:241` |
| P1 | `fix(dashboard): enforce soul secret-lint server-side, not just cosmetic client warning` | security | `apps/dashboard/src/lib/soul-secret-lint.ts:1` |
| P1 | `docs(architecture): fix deployment.md container topology (9 → 20 containers)` | ssot | `docs/architecture/deployment.md:7` |
| P1 | `docs(architecture): fix security-routing.md ADR-090 status (planned → shipped)` | ssot | `docs/architecture/security-routing.md:87` |
| P1 | `fix(deploy): scope network.roxabi host_roles to include M2 (llm-worker/image-worker)` | ssot | `deploy/quadlet.toml:167` |
| P1 | `fix(obs): move FleetReporter report-construction inside the crash guard` | week-subsystem | `packages/roxabi-obs/src/roxabi_obs/reporter.py:82` |
| P1 | `fix(ci): add packages/roxabi-obs(+roxabi-satellite) to pyright include` | week-subsystem | `pyproject.toml:138` |
| P1 | `fix(ci): wire packages/roxabi-obs/tests into an actual CI test job` | week-subsystem | `pyproject.toml:109` |
| P1 | `fix(dashboard): useAgentStatus.ts picks rows[0] instead of the selected agent` | week-subsystem | `apps/dashboard/src/hooks/useAgentStatus.ts:21` |
| P1 | `fix(dashboard): bulk agent-status RPC stops defaulting non-targeted agents to harness=claude-cli` | week-subsystem | `src/factory/bootstrap/factory/dashboard_rpc.py:229` |
| P1 | `fix(dashboard): BFF error-mapping catches NATS RPC timeout/no-responders as 503` | error-async | `src/factory/dashboard/routes/bff_common.py:32` |
| P1 | `fix(hub): fleet_digest.py — move blocking sync file I/O off the event loop` | error-async | `src/factory/nats/fleet_digest.py:54` |

### Top-10 quick wins (high impact / low effort)

1. **Add `packages/roxabi-obs` to pyright include** (`pyproject.toml:138`) — 1-line config fix, closes a known CI typecheck blind spot hiding a real type error. (week-subsystem)
2. **Wire `packages/roxabi-obs/tests` into a real CI job** (`pyproject.toml:109`) — testpaths already declared, just needs a job to invoke it; restores a shutdown-fix regression test. (week-subsystem)
3. **Fix `deployment.md` container count** (9 → 20) — pure doc edit, removes a stale fact cited by other docs. (ssot)
4. **Fix `security-routing.md` ADR-090 status** (planned → shipped) — pure doc edit, prevents future agents from re-litigating already-shipped work. (ssot)
5. **Patch `tools/check_str_exc_bus_bound.sh`** regex + add `bootstrap/` to scan roots — closes 2 already-verified false-negatives (socialmedia adapter, dashboard admin RPCs) in one gate change. (security + architecture)
6. **Harden `deploy/quadlet/factory-cloudflared.container`** — add `ReadOnly=true` + pin the image tag (currently floating `:latest`) on the one internet-facing unit missing both. (security)
7. **Fix `useAgentStatus.ts` `rows[0]` selection bug** — small frontend diff, fixes a visibly-wrong online/harness badge in the operator cockpit. (week-subsystem)
8. **Backport the missing NATS ACL grants** (`$JS.API.STREAM.NAMES` + `CONSUMER.INFO`) to telegram/discord-adapter — the fix pattern already exists (same bug was just fixed for the dashboard). (deploy)
9. **Fix `OpsPage.tsx` hardcoded French log-preset labels** — small diff, brings the page in line with the already-shipped react-i18next EN/FR system. (week-subsystem)
10. **Catch up `packages/roxabi-contracts/CHANGELOG.md`** for the 0.11.0 → 0.13.0 bumps — restores the only human-context satellite reviewers get from the auto-merge contracts-bump PR. (contracts)

### Links to domain detail

- [`by-domain/architecture.md`](by-domain/architecture.md) — 9 findings, subscore 60
- [`by-domain/axial-drift.md`](by-domain/axial-drift.md) — 4 findings, subscore 76
- [`by-domain/security.md`](by-domain/security.md) — 13 findings, subscore 15
- [`by-domain/ssot.md`](by-domain/ssot.md) — 22 findings, subscore 40
- [`by-domain/deploy.md`](by-domain/deploy.md) — 7 findings, subscore 33
- [`by-domain/week-subsystem.md`](by-domain/week-subsystem.md) — 17 findings, subscore 35
- [`by-domain/contracts.md`](by-domain/contracts.md) — 2 findings, subscore 90
- [`by-domain/error-async.md`](by-domain/error-async.md) — 5 findings, subscore 58

---

# Part II — Full domain detail (all findings verbatim)


---

## Architecture domain — audit findings (2026-06-30)

Source: `arch-core`, `arch-adapters`, `arch-bootstrap`, `arch-infra` finders. 9 findings after REFUTED removal, 0 duplicates merged (each finding sits on a distinct file/root-cause; `arch-bootstrap-2` and `arch-bootstrap-3` both live under `src/factory/bootstrap/` but address unrelated root causes — omp-rpc construction gap vs. nats_connect error-handling duplication — so they were kept separate, not merged).

Severity bucket = `adjusted_severity` where a verify pass ran (CONFIRMED findings), else falls back to the finder's original `severity` for UNVERIFIED findings. Ranking within a bucket: CONFIRMED > PLAUSIBLE > UNVERIFIED.

### P0 — Critical

None.

### P1 — High

| # | File:line | Verdict | Title | Evidence | Recommendation |
|---|---|---|---|---|---|
| 1 | `src/factory/infrastructure/stores/kv/_kv_keys.py:13` | **CONFIRMED** | `kv_safe_part` is non-injective: distinct `RoutingKey` pool_ids collide on the same sanitized NATS-KV key | `_UNSAFE_RE = re.compile(r"[^-/_=.A-Za-z0-9]")` collapses both the `:` field delimiter used by `RoutingKey.to_pool_id()` (`core/hub/hub_protocol.py:136-142`) and literal `_` already legal inside `bot_id` (`^[A-Za-z0-9_-]{1,48}$`, enforced in `infrastructure/kv/bot_roster.py:22` and 3 other sites) to the same output char. Reproduced directly: `to_pool_id('telegram','support_bot','42')` and `to_pool_id('telegram','support','bot_42')` both sanitize to `idx.telegram_support_bot_42`. Consumed verbatim by `infrastructure/stores/jobs/active_jobs_kv.py:75-77` (`_idx_key`, CAS singleton index — `refresh()` at line 262 does an unconditional `kv.put` with no ownership check, so a colliding pool's refresh silently flips the index to a different job_id) and `infrastructure/stores/kv/message_index_kv.py:103,109` (`upsert`/`resolve` reply-to-resume index — collision + repeated platform_msg_id resolves a reply to the wrong `session_id` across unrelated bots/scopes). Reopens the exact outage class commit `7cc834f7` fixed (colon→`InvalidKeyError`), via aliasing instead of rejection. No regression test asserts key-distinctness for two different pool_ids (`tests/infrastructure/stores/test_message_index_kv.py`, `test_active_jobs_kv.py` only assert validity, not distinctness). | Make `kv_safe_part` injective for the join use case — percent/hex-encode unsafe bytes (mirroring `KvSentSet`'s hex-encoding in `adapters/nats/jetstream_audio_dedup.py:125`) instead of collapsing to a shared `_`, or sanitize+join each `RoutingKey` field independently with a delimiter that cannot appear inside a sanitized field. Add an adversarial injectivity regression test. |

### P2 — Medium

| # | File:line | Verdict | Title | Evidence | Recommendation |
|---|---|---|---|---|---|
| 2 | `src/factory/adapters/socialmedia/adapter.py:295` | **CONFIRMED** (downgraded high→medium) | socialmedia adapter leaks raw exception text into bus-bound `error` field, bypassing SanitizedError discipline and evading the `str(exc)` enforcement gate | `except PostizApiError as exc: return build_list_groups_error(req, str(exc), ...)` / `except ValidationError as exc: return build_publish_error(req, error=str(exc))` (lines 129-130, 179-180, 290-295) populate `SocialMediaPublishResponse.error`/`ListGroupsResponse.error`/`ListIntegrationsResponse.error` (`packages/roxabi-contracts/.../socialmedia/models.py:73,86,124`), none of which carry the `field_validator` scrub (`_sanitize_message`/`_sanitize_detail`) that `WorkerError.message`/`.detail` get (`errors.py:122-132`). `tools/check_str_exc_bus_bound.sh` independently reproduced as exiting 0 clean — its regex only matches `message=str(exc...`, not `error=str(exc)` or bare positional args, so this is real gate-evasion. Confirmed cross-bus exposure (`adapter.py:303-304` → `self.reply()` → NATS publish) and a concrete downstream consumer that uses the *raw* field (`nats/socialmedia/nats_socialmedia_client.py:186-187` raises `SocialMediaUnavailableError(decoded.error or ...)`). Downgrade rationale (traced during verify): the actual string content reaching `str(exc)` is third-party Postiz API response text / internal data-shape descriptions, never request headers/secrets — so blast radius is information-disclosure of third-party error text to the LLM/hub layer, not credential exposure. | Route `PostizApiError`/`ValidationError` text through `SanitizedError.from_message()` (or `scrub_credentials()`+truncate, matching `WorkerError`'s validator) before assigning to `.error`; add a `field_validator` on the socialmedia response models' `error` field. Extend `check_str_exc_bus_bound.sh`'s regex to catch `error=str(exc`/`error=f"...{exc` and similarly-named bus-bound kwargs. |
| 3 | `src/factory/core/agent/agent.py:134` | UNVERIFIED | Core domain modules import `sqlite3`/`aiosqlite` directly at module scope just to type driver-specific `except` clauses, leaking persistence tech into the hexagonal core | `core/agent/agent.py:4,10,134` (`except (sqlite3.Error, aiosqlite.Error, OSError, RuntimeError):`), `core/pool/pool_observer.py:4,9,23-30` (`_TURN_PERSIST_ERRORS` typed to sqlite3/aiosqlite), `core/tts_dispatch.py:14,18,205` — all import concrete driver modules outside `TYPE_CHECKING` even though I/O goes through `AgentStoreProtocol`/`TurnStoreProtocol`/`PrefsStore` ports. Contradicts `docs/architecture/target-architecture.md` ("Domain Core never imports... framework drivers"); none of the 3 sites carry a `DEBT:` tag (unlike 60 other tracked debt markers in `core/`) → untracked drift. | Have store protocols (or a thin infra-boundary adapter) translate driver exceptions into a single domain-level `StoreUnavailableError` so core code never names `sqlite3`/`aiosqlite`; future-proofs for in-flight non-SQLite store migrations (e.g. `MessageIndexKvStore` per #1059). |
| 4 | `src/factory/core/ports/tts.py:15` | UNVERIFIED | Two-way `TYPE_CHECKING` import cycle between `core/ports` (driven-port layer) and `core/agent`/`core/messaging` contradicts "ports owns domain types" design | `core/ports/tts.py:15` imports `AgentTTSConfig` from `core.agent.agent_config`; `core/ports/outbound_listener.py:18` imports `InboundMessage` from `core.messaging.message` (both TYPE_CHECKING). Reverse direction: `core/agent/agent.py:16-17` imports `STTProtocol`/`TtsProtocol` from `core.ports`; `core/messaging/push_guard.py:21` imports `OutboundListener` from `core.ports` (also TYPE_CHECKING). AGENTS.md states ports/ is "the single owner of domain types" — but ports depends back on the packages that depend on it, a real circular coupling invisible to import-linter (governs `factory.core` as one opaque layer, not intra-core direction). | Move `AgentTTSConfig` (and any other value object a protocol signature needs) into `core/ports` itself, or define a narrower structural type local to `ports/tts.py`, so the dependency arrow becomes one-directional (`agent → ports`). |
| 5 | `src/factory/bootstrap/factory/unified.py:82` | UNVERIFIED | Unified (`factory start`) root registers the omp-rpc backend but can never construct its only consumer in-process | `unified.py:82` unconditionally builds `OmpRpcDriver(nc)` and registers it as the `"omp-rpc"` provider for any agent, but never constructs an `OmpWorker`/`OmpPool` — that only happens in `_bootstrap_omp_standalone` (`worker_standalone.py:175-211`), reached only via the separate `factory adapter omp` CLI command, which `_bootstrap_unified` never invokes. `worker_standalone.py:190-191` documents omp_rpc as "a container image dep (absent from pyproject.toml)" — structurally impossible to co-locate. `docs/QUICKSTART.md:100` documents `factory start` as the standard single-process run. `tests/bootstrap/test_unified.py`/`test_unified_clipool.py` have zero omp references → untested path. | Fail fast (or warn loudly) at unified startup when any `agent_config.llm_config.backend == "omp-rpc"`, since the unified process can never satisfy it; or document the limitation next to the quickstart instructions to avoid a silent ~600s per-job timeout. |
| 6 | `src/factory/bootstrap/standalone/worker_standalone.py:203` | UNVERIFIED | `nats_connect()`-or-exit wiring duplicated across 5 standalone roots; 2 of 5 missing the error handling | `hub_standalone.py:70-78`, `worker_standalone.py:136-143` (turn-writer), `adapter_standalone.py:78-89` (telegram/discord/web) each wrap `nats_connect()` in try/except → `sys.exit(f"Failed to connect to NATS at {url!r}: {exc}")`. The clipool root (`worker_standalone.py:90-102`) wraps the call in a bare try/finally with no except; the omp root (`worker_standalone.py:203`) calls `nats_connect(...)` entirely outside any try block. `factory-clipool.container` and `factory-omp.container` (deploy/quadlet) will raise an unhandled traceback on NATS-unreachable startup instead of the clean single-line `sys.exit` every other root produces. | Extract a shared `connect_or_exit(nats_url, identity_name)` helper (same consolidation pattern already applied to telegram/discord/web wiring in `_standalone_wiring_common.py:wire_bot_common`) and use it at all 5 call sites to remove duplication and close the clipool/omp error-handling gap. |

### P3 — Low

| # | File:line | Verdict | Title | Evidence | Recommendation |
|---|---|---|---|---|---|
| 7 | `src/factory/core/tts_dispatch.py:119` | UNVERIFIED | TTS dispatch logic split across a confusingly-named pair of files in different directories | `tts_dispatch.py:119` defines `AudioPipeline`, the actual TTS-synthesis-and-dispatch implementation used exclusively by the hub outbound path (per its own docstring), yet lives at top level of `core/` rather than under `core/hub/outbound/` alongside `outbound_tts.py:24`'s `TtsDispatch` class (which only holds a reference to an `AudioPipeline`, imported via `TYPE_CHECKING` at `outbound_tts.py:21`). Near-identical names (`tts_dispatch.py` module vs `TtsDispatch` class living elsewhere) hurt navigability. | Relocate `tts_dispatch.py` (`AudioPipeline`) into `core/hub/outbound/` next to `outbound_tts.py` (`TtsDispatch`) so the TTS dispatch concern is co-located, consistent with `outbound/`, `middleware/`, `pipeline/` already being organized as hub sub-concerns. |
| 8 | `src/factory/inbound/AGENTS.md:27` | UNVERIFIED | `inbound/AGENTS.md` still documents now-removed importlinter exemptions for adapter imports (stale doc) | Doc states inbound-no-adapters violations are "tagged `DEBT:inbound-adapters-transition`/`DEBT:inbound-adapters-wireparser`... listed as `ignore_imports`" — but current `.importlinter` (lines 150-157) `inbound-no-adapters` contract has zero `ignore_imports` entries (removed in commit `6c5e083`). `grep -rn 'DEBT:inbound-adapters'` across `src/` finds zero occurrences; `inbound/*.py` now use `TYPE_CHECKING`-only imports + a `_<Platform>Normalizer` Protocol (e.g. `wire_parser_discord.py:14-16`). File last touched 2026-06-23, three weeks after the fix landed — staleness survived an unrelated edit. | Delete or update the stale "Known violations are tagged..." sentence in `inbound/AGENTS.md` to reflect zero exemptions (fully enforced, no debt remaining). |
| 9 | `packages/roxabi-satellite/src/roxabi_satellite/tokens.py:11` | UNVERIFIED | NATS identifier-validation regexes duplicated across `roxabi-nats` and `roxabi-satellite` with no parity test | `roxabi_satellite/tokens.py:1-31` is a verbatim copy of `roxabi_nats/_validate.py:1-62` (`validate_nats_token`/`validate_nats_single_token`, identical `_NATS_IDENT`/`_NATS_SINGLE_TOKEN` regexes), intentional per ADR-045 boundary but with no test asserting byte-identity between copies (`grep` found zero tests importing both modules together). A future hub-side validation tightening (e.g. for the `kv_safe_part` collision above, finding #1) would silently fail to propagate to `roxabi-satellite`'s `validate_nats_single_token`, used by `nats/fleet_store.py:14,72` to gate inbound fleet `container_name` reports. | Add a cross-package parity test (e.g. in `packages/roxabi-satellite/tests`) diffing the regex patterns/behavior on a shared fixture set, or generate `roxabi_satellite/tokens.py` from `roxabi_nats/_validate.py` at build/release time instead of hand-copying. |

### Notes

- 2 of 9 findings independently verified (CONFIRMED): #1 (`kv_safe_part` collision, high — held at high) and #2 (socialmedia `str(exc)` leak, downgraded high→medium after tracing actual exception content to third-party API text, not secrets).
- 7 of 9 findings are UNVERIFIED (no verify pass ran) — treat as leads, not established facts, until independently checked.
- Cross-cutting pattern: 2 of the 9 findings (#1, #9) point at the **same underlying class of risk** — NATS-KV / NATS-identifier sanitization correctness lacking adversarial/parity regression tests. Worth a single follow-up issue covering both (`kv_safe_part` injectivity test + `roxabi-nats`/`roxabi-satellite` regex parity test) rather than two.
- `str(exc)` bus-bound discipline (ADR-073/#1212) has a real, repo-wide enforcement gate (`tools/check_str_exc_bus_bound.sh`) that is itself incomplete (misses `error=str(exc)`/bare positional forms) — finding #2's recommended gate regex extension should be treated as a standing action item, not just a local fix to `socialmedia/adapter.py`.


---

## Axial-drift domain — audit synthesis (2026-06-30)

4 raw findings (2 finders: `axial-repo`, `axial-week`) → **4 deduped** (no exact file-region collisions; one related-but-distinct cluster flagged below, not merged — see Duplication table). Ranked by `adjusted_severity` (fallback `severity`); ties broken CONFIRMED > PLAUSIBLE > UNVERIFIED.

### Severity counts (deduped)

| Severity | Count |
|---|---|
| critical | 0 |
| high | 0 |
| medium | 2 |
| low | 2 |

**debt_subscore: 76/100** (100=pristine). No critical/high axial-boundary violations this cycle. The lead finding (#1) is the only fully CONFIRMED item — a real, independently-verified triplicated `_validate_worker_error()` helper across the claude/omp/cli_pool codec stage, with one of the three copies already dead code left over from a migration. The remaining three findings are UNVERIFIED (not independently re-checked this pass) but evidence-backed pattern matches consistent with this repo's documented "target-axis-trap" failure mode (ADR-073): cross-cutting concerns (platform-identity rendering, a hub accessor helper, missing per-subsystem stage-axis docs) re-implemented or omitted per call-site instead of factored onto a shared axis.

---

### P0 — Critical

None.

### P1 — High

None. (`axial-repo-001` was originally filed as `high` but adjusted to `medium` on verification — see row below; behaviorally identical triplicate today, no live divergence, prospective risk only.)

---

### P2 — Medium

| # | File:line | Verdict | Title | Evidence (condensed) | Recommendation |
|---|---|---|---|---|---|
| 1 | `src/factory/llm/claude_job_codec.py:24` (+ `omp_job_codec.py:18`, `cli_pool_codec.py:41`) | CONFIRMED | `_validate_worker_error()` triplicated near-verbatim across 3 sibling codec modules instead of living once on the stage axis | Independently re-verified: all three functions present at cited lines, 12/13 lines byte-identical (only the log-prefix string differs). `ClaudeJobCodec` (imported `drivers/claude_rpc.py:21`) and `OmpJobCodec` (imported `drivers/omp_rpc.py:24`) are both live/wired in bootstrap (`hub_assembly.py`, `unified.py`, `providers.py`). `CliPoolCodec` confirmed dead — its only importer outside itself is `tests/llm/test_cli_pool_codec.py`; the live NATS clipool path uses `CliNatsCodec` instead. Commit `1c8d18ad` (2026-06-28, "unify claude-cli on JobEnvelope pub/sub phase 2") added `claude_job_codec.py` by copying the existing `omp_job_codec.py` pattern rather than extracting it — the very PR that unified transports re-introduced the duplication at the validation layer. ADR-073 explicitly names this failure mode ("a shared helper changes nothing structural... the next concern re-creates the duplication") and project precedent treats 3 sibling copies as the actionable threshold. No functional divergence today — all 3 copies share identical `KNOWN_CODES` guard + `worker.internal` fallback — so risk is prospective (future fix-one-miss-others), not a live bug; downgraded high→medium on that basis. | Hoist `_validate_worker_error` (KNOWN_CODES guard + fallback + logging) into one shared stage-axis module (e.g. `roxabi_contracts.errors` or new `factory.llm._codec_common`); parameterize only the log-prefix label. Delete orphaned `cli_pool_codec.py` (`CliPoolCodec`, unused since the JobEnvelope migration) or document why it's intentionally retained. |
| 2 | `apps/dashboard/src/components/agents/AgentsListPanel.tsx:287` (+ `:382`, `admin_rpc.py:90`, `dashboard_agents_rpc.py:53`, `AdminPage.tsx:146`) | UNVERIFIED | Telegram/Discord/Email presence rendering hardcoded field-by-field across 5 sites instead of iterating one platform list (target-axis-trap) | All 5 sites introduced/expanded by this week's PR #2070 (dashboard admin users/agents). `AgentsListPanel.tsx` duplicates the identical 3-badge block twice in the same file (card view :285-296, table view :382-389). Contract itself (`packages/roxabi-contracts/src/roxabi_contracts/dashboard/models.py:11`) declares `PlatformTag = Literal["telegram","discord","web"]` but `DashboardAgentSummary` only carries `has_telegram`/`has_discord`/`has_email` — `web` has no corresponding flag anywhere, supporting the claim that adding a platform requires touching every hardcoded site. ccc search corroborates structural similarity (0.66/0.62, probable band) but this row was not independently re-verified this pass (no direct diff/import-graph check beyond the original finder's evidence). | Introduce one canonical `SUPPORTED_PLATFORMS` list (driven by `PlatformTag`) + a single shared `<PlatformBadges>` component + one backend `platform_identities_for(...)` helper consumed by both `admin_rpc.py` and `dashboard_agents_rpc.py`. Decide whether `web` is a real identity type and wire it through everywhere or drop it from `PlatformTag`. |

---

### P3 — Low

| # | File:line | Verdict | Title | Evidence (condensed) | Recommendation |
|---|---|---|---|---|---|
| 3 | `src/factory/bootstrap/factory/dashboard/admin_rpc.py:45` (+ `dashboard_agents_rpc.py:41`) | UNVERIFIED | `_agent_store(hub)` helper byte-identically duplicated across two dashboard RPC modules split out of `dashboard_rpc.py` for the file-length gate | 6-line function (`getattr(hub, "_agent_store", None)` → `next(iter(hub.agent_registry.values()), None)` fallback) claimed byte-identical via `diff` in both modules; both born from this week's PR #2070 file-length split. Unlike this finding, the split kept `_blob_store`/`_bot_store`/`_user_store`/`_grant_store` single-defined — so this is plausibly a one-off miss in an otherwise-correct extraction, not a systemic pattern. Not independently re-verified this pass. | Move `_agent_store(hub)` into a shared dashboard RPC helpers module (e.g. `dashboard/_hub_accessors.py`) imported by both `admin_rpc.py` and `dashboard_agents_rpc.py`, mirroring how the other hub-accessor helpers are kept single-defined. |
| 4 | `src/factory/ingress/:1` | UNVERIFIED | New `factory.ingress` subsystem (ADR-096, this week's highest-churn new pipeline) has no AGENTS.md/CLAUDE.md documenting the stage-axis invariant (shared verify/normalize/payload logic vs. per-connector thin config) | `find src/factory/ingress -iname '*.md'` returns nothing per the finder; every comparable subsystem (`blobstore/AGENTS.md`, `nats/AGENTS.md`, `roxabi-obs/AGENTS.md`) carries one. Low risk today since current code reportedly already follows the correct pattern (shared `verify.py`/`normalize.py`/`payload.py`, thin `connectors/*.py`) — the gap is the missing guardrail for the *next* connector (e.g. planned Vercel connector), not a present violation. Not independently re-verified this pass (doc-existence claim only, not cross-checked against current connector code for drift). | Add `src/factory/ingress/AGENTS.md` stating the Connector protocol contract (verify/parse_external_id/apply_lifecycle/normalize stay thin per-target; shared stage logic lives in verify.py/normalize.py/payload.py). |

---

### Duplication table (cross-domain notes for reduce step)

| Cluster | Members | Same root cause? | Note |
|---|---|---|---|
| Dashboard PR #2070 hub-accessor/identity split | #2 (`apps/dashboard/.../AgentsListPanel.tsx`, `admin_rpc.py:90`, `dashboard_agents_rpc.py:53`) and #3 (`admin_rpc.py:45`, `dashboard_agents_rpc.py:41`) | Related, not identical — both stem from this week's `dashboard_rpc.py` file-length-gate split (PR #2070) leaving cross-cutting concerns (platform-identity rendering vs. hub-accessor helper) un-consolidated in the same two modules, but they touch different functions/line-ranges and different defect shapes (N-platform enumeration vs. simple 1-helper duplication). **Not merged** — kept as separate findings; flagged here so the reduce step can note "PR #2070's dashboard-RPC split systematically skipped extracting shared helpers" as one cross-cutting root cause if other domains (e.g. ssot, contracts) hit the same admin_rpc.py/dashboard_agents_rpc.py files this cycle. |
| Codec-stage triplication (#1) | `claude_job_codec.py`, `omp_job_codec.py`, `cli_pool_codec.py` | Single root cause, single finding | Already one finding (#1 above) — listed here only because it is the domain's clearest "target-axis-trap" exemplar per ADR-073 and may be useful as a canonical example if the reduce step writes a cross-domain "target-axis-trap" pattern summary. |

ADR-073 ("Axial Stage-of-Pipeline Decomposition") is the architecture-doctrine SSoT this whole domain is graded against: it explicitly predicts that a single shared-helper fix without an owning stage-axis module just relocates the duplication, and treats 3 sibling copies of cross-cutting logic as the actionable "three-strikes" threshold. Finding #1 is a textbook hit on that threshold; #2-#4 are weaker (UNVERIFIED) instances of the same class — N call-sites re-declaring a platform enum, a duplicated accessor, and a missing stage-boundary doc.


---

## Security domain — synthesis (2026-06-30 full audit)

Source: 19 raw findings from 5 finders (`sec-ingress`, `sec-authz`, `sec-secrets-acl`, `sec-blobstore-soul`, `sec-dashboard`), REFUTED already removed upstream. After dedup: **13 distinct findings** (6 raw findings collapsed into one P0 root cause hit independently by 5 of the 5 finders — strong cross-finder signal). This supersedes an earlier narrower pass of this same file that only covered the `sec-dashboard` finder's 2 raw findings.

### Headline

The dashboard BFF (`src/factory/dashboard/routes/bff_admin.py`, `bff_agents.py`, and the job-control routes in `bff.py`) shipped this week (PR #2070, #1772/#1773) with **zero authentication** on every mutating route — admin user creation, agent-grant self-service, agent persona/soul overwrite, and live-job text injection. `require_operator` exists and is correctly wired on the sibling `connectors.py` router, proving this is an incomplete rollout, not a designed trust boundary. Five independent finders converged on the same root cause from different entry points (admin/user, soul/persona, jobs/steer). This is the dominant signal in the domain and the clear P0.

### P0 — Critical

| ID | File:line | Verdict | Evidence | Recommendation |
|---|---|---|---|---|
| **MERGED** (sec-authz-001, sec-secrets-acl-1, sec-blobstore-soul-01, sec-blobstore-soul-02, sec-dashboard-001, sec-dashboard-003) | `src/factory/dashboard/routes/bff_admin.py:24-64`, `src/factory/dashboard/routes/bff_agents.py:22-95` | CONFIRMED (6/6 independent reads) | `register_admin_routes()` (`POST /admin/users`, `PATCH /admin/users/{id}`, `GET /admin/access`) and `register_agent_routes()` (`PUT /agents/{name}/soul`, `POST /agents/{name}/soul/preview`, `POST/PATCH /agents`) declare **no** `Depends(require_operator)` — contrast `routes/connectors.py`, which gates every route with `Depends(require_operator)`. `app.py` (`create_dashboard_app`) installs no app-level auth middleware; hub-side NATS RPC handlers (`admin_rpc.py`, `dashboard_agents_rpc.py`) perform no caller-identity check either, so the gap can't be papered over at the transport layer. Dashboard binds `${TAILSCALE_IPV4}:8765` (Tailnet-reachable, not internet). Concrete blast radius, all independently traced to live call chains: (a) **privilege escalation** — `POST /admin/users` body `agents:list[str]` flows to `grant_store.grant(agent, principal, capability=USE)` (`admin_rpc.py:100-125`), the exact `agent_grants` table `AuthorizeAgentMiddleware` (ADR-090 stage 7) trusts for every inbound message; (b) **persona hijack** — `PUT /agents/{name}/soul` overwrites the agent's system prompt via `soul_ops.put_soul_document`, hot-reloaded into the live `AgentBase` on next `process()` and consumed by job dispatch (`dashboard_jobs_rpc.py:75`); (c) **identity disclosure** — `GET /admin/access` enumerates user_ids with no credential. `require_operator` itself also fails open (`auth.py:44-45`) when `FACTORY_DASHBOARD_OPERATOR_TOKEN` is unset (shipped commented-out in `deploy/env/web.env.example`), so even configuring the missing `Depends()` needs the token set to be effective. `deploy/AGENTS.md`'s #1992 residual-risk note pre-dates this surface (PR #2070 landed after) and only scopes to chat/`sessions*` — it does not cover admin/agent/soul routes. Net-new code (commit `411ee09d`, "feat(dashboard): redesign operator console with users and agents admin") — not a regression, the check was simply never added for this router family. | Wire `Depends(require_operator)` onto every route in `bff_admin.py` and `bff_agents.py` (mirror `connectors.py`); add a hub-side principal check in `admin_rpc.py`/`dashboard_agents_rpc.py` as defense-in-depth (NATS callers shouldn't bypass the BFF gate); make `FACTORY_DASHBOARD_OPERATOR_TOKEN` a required, not optional, secret for `factory-dashboard`; extend `deploy/AGENTS.md`'s #1992 note to explicitly cover this surface until fixed. |
| sec-dashboard-004 | `src/factory/bootstrap/factory/dashboard_jobs_rpc.py:100` (route: `src/factory/dashboard/routes/bff.py:136-148`) | CONFIRMED | `POST /api/bff/jobs/steer` has no auth dependency and no ownership check: `handle_jobs_steer` does `subject = jobs_steer(req.job_id); await nc.publish(subject, req.text.encode())` with zero validation that `job_id` belongs to the requester — only a NATS-subject character sanitizer (`validate_job_token`), not authz. `GET /api/bff/jobs` (also unauthenticated) enumerates every active job system-wide. The published text is decoded and fed directly into the live agent's `prompt_and_wait` stream (`_rpc_bridge_steer.py:27-43`) — real text-injection into another principal's in-flight Telegram/Discord/web conversation. Same root cause family as the P0 above (missing `require_operator`) but a structurally distinct route file (`bff.py` jobs endpoints, not `bff_admin`/`bff_agents`) and a distinct IDOR mechanism (no ownership check even if generic auth were added), so kept as a separate entry. | Require operator auth on `/api/bff/jobs/launch` and `/api/bff/jobs/steer`; add an ownership/ACL check in `handle_jobs_steer` verifying the requester owns the job's `pool_id`/agent before publishing to `jobs_steer(job_id)`. |

### P1 — High

| ID | File:line | Verdict | Evidence | Recommendation |
|---|---|---|---|---|
| sec-dashboard-002 | `src/factory/bootstrap/factory/dashboard/admin_rpc.py:241` | CONFIRMED (severity adjusted critical→high: Tailnet-only, not internet; impact exceeds what #1992 risk-acceptance explicitly covered) | `handle_admin_user_patch` calls `user_store.set_platform_identity(user_id, platform, uid)` for any `user_id` the (unauthenticated, see P0) caller names. `set_platform_identity` (`user_store_profile.py:131-191`) only rejects if the platform key is already linked to a *different* user — it never checks the caller is entitled to mutate the target `user_id`. An attacker who learns a privileged `user_id` (trivially, via the also-unauthenticated `GET /admin/access`) can rebind their own Telegram/Discord UID onto it and inherit its `agents` USE grants via `resolve_user_id` → `AgentGrantStore._expand_user_principals` → `AuthorizeAgentMiddleware` — full impersonation of an existing factory user. This is a logic bug independent of the P0 auth gap: fixing `require_operator` alone doesn't stop an operator-authenticated-but-unprivileged caller from rebinding someone else's identity unless ownership is also checked. | Fix #1 (P0) is the primary mitigation. Defense-in-depth: `set_platform_identity`/`handle_admin_user_patch` should require the caller already own the target identity (or be an explicit superadmin action), not be a side effect of any profile-patch RPC. |
| sec-blobstore-soul-03 | `apps/dashboard/src/lib/soul-secret-lint.ts:1` | CONFIRMED | Self-documented as a "soft warning" — `secretWarning` is computed and rendered as a destructive `Alert` in `AgentsPage.tsx:280`, but the Save button's `disabled` prop (line 287) never references it (`disabled={saveMut.isPending \|\| docBytes > 49152}`), so a detected secret never blocks save even through the UI. Server-side, `soul_ops.put_soul_document` → `validate_soul_document_bytes` (`core/persona.py:97-103`) checks byte length only — zero secret-pattern scanning anywhere in the Python backend. Combined with the P0 (unauthenticated PUT/GET), a secret pasted into persona text becomes a permanent fragment of the composed system prompt (sent to every LLM call) and is readable via the also-unauthenticated GET. | Gate the Save action on `secretWarning` (require explicit confirm-override), and/or port the regex set into a server-side check inside `put_soul_document()`/`validate_soul_document_bytes()` so it can't be bypassed by calling the BFF/RPC layer directly. |

### P2 — Medium

| ID | File:line | Verdict | Evidence | Recommendation |
|---|---|---|---|---|
| sec-authz-002 | `src/factory/core/hub/middleware/middleware_authz.py:66` | CONFIRMED (severity adjusted high→medium: this is an availability/lockout bug, not an exposure) | ADR-090 §1 promises `[admin].user_ids` retain authorization bypass "until explicitly narrowed in a follow-up ADR." `AuthorizeAgentMiddleware.authorize()` never references `msg.is_admin` (zero hits in file) despite the signal being populated upstream at stage 2 (`ResolveTrustMiddleware`/`Authenticator.resolve()`). `AgentGrantStore.authorize()` has no admin-aware branch and no grant rows are ever auto-seeded for `admin_user_ids` (`auth_seeding.py:4`: "store-sourced only — no boot seed"). Stage 7 (authz) precedes stage 9 (command-level admin check), so a denied admin's message never reaches the place that *would* recognize them as admin. Net effect: an operator in `[admin].user_ids` without an explicit `factory agent grant` row is refused exactly like a stranger — the documented bypass is stale, not implemented. | Either implement the bypass (short-circuit to allow when `msg.is_admin`) inside `AuthorizeAgentMiddleware`/`AgentGrantStore`, or update ADR-090 to remove the stale promise and require operators to self-grant via CLI. |
| sec-secrets-acl-2 + sec-secrets-acl-3 (merged — same gate, two independent blind spots) | `tools/check_str_exc_bus_bound.sh:12,27`; leak sites `src/factory/adapters/socialmedia/adapter.py:129-130,179-180,290-291` and `src/factory/bootstrap/factory/dashboard/admin_rpc.py:205,257`, `dashboard_agents_rpc.py:260` | CONFIRMED (severity adjusted high→medium both: leaked content is bounded — third-party SaaS error text / benign hand-authored validation strings, not internal secrets/stack traces) | Two independently-reproduced gate gaps in the same script: (1) **regex blind spot** — the gate only matches `message=str(exc...)` kwarg syntax; `socialmedia/adapter.py` uses positional/non-keyword forms (`build_*_error(req, str(exc), worker_error=worker_error_from_http_provider(exc.status_code, str(exc)))`) that flow into the bus-bound `SocialMediaListGroupsResponse.error`/`...PublishResponse.error` fields (no Pydantic sanitizer, unlike `WorkerError.message` which does get scrub+truncate) — reproduced: running the gate's exact grep pattern against the file returns zero matches despite confirmed violations. (2) **missing scan root** — `STR_EXC_SCAN_ROOTS` never includes `src/factory/bootstrap/` or `src/factory/core/hub/`; `admin_rpc.py`/`dashboard_agents_rpc.py` return dict-literal `{"message": str(exc)}` (also unmatched syntax) that reach NATS replies via `_wrap()` → `msg.respond(json.dumps(result))`, and ultimately an HTTP 409 to the dashboard client — reproduced: `bash tools/check_str_exc_bus_bound.sh` exits 0 ("OK") even with `STR_EXC_SCAN_ROOTS` widened to include bootstrap/. Traced exception sources for (2) are low-sensitivity, hand-authored validation strings (echoing the caller's own input), not raw internal exceptions. | Broaden the gate regex to flag `str(exc)`/`f"{exc}"`/`repr(exc)` regardless of keyword name or dict-literal vs kwarg syntax (or move to an AST-based check); add `src/factory/bootstrap/` and `src/factory/core/hub/` to `STR_EXC_SCAN_ROOTS`; scrub `str(exc)` in `socialmedia/adapter.py`'s `error=` fields before they reach `build_*_error()`. |
| sec-ingress-01 | `deploy/quadlet/factory-cloudflared.container:8` | CONFIRMED (severity adjusted high→medium: `UserNS=keep-id:1500` claim overstated as "mandatory ∀ unit" — other 3rd-party images also lack it; baseline rootless-Podman isolation + existing `DropCapability=all`+`NoNewPrivileges=true` already present) | The internet-facing Cloudflare Tunnel front door is the only unit in `deploy/quadlet/*.container` (besides the documented Langfuse ADR-092 carve-out) missing `ReadOnly=true`, and the only unit anywhere in the deploy tree pinned to a floating `Image=docker.io/cloudflare/cloudflared:latest` tag with `Label=io.containers.autoupdate=registry` — every other third-party image (langfuse:3, postgres:17, redis:7-alpine, otel-collector:0.120.0, factory-nats by digest) is version/digest-pinned. Confirmed via repo-wide grep; no carve-out documented anywhere for cloudflared specifically. | Add `ReadOnly=true` (cloudflared's binary/config don't need a writable rootfs) and `UserNS=keep-id:uid=1500,gid=1500` to match the rest of the fleet; pin `Image=` to a specific version/digest and bump deliberately, or document an explicit carve-out in `deploy/AGENTS.md` if a floating tag is genuinely required. |
| sec-secrets-acl-4 | `deploy/nats/acl-matrix.json:161` | UNVERIFIED | `web-adapter` identity is granted a single `factory.dashboard.>` publish wildcard spanning both ordinary and the new admin-tier subjects (`admin.access`, `admin.user.create`, `admin.user.patch`, `agents.soul.put`). Since the dashboard process is the sole holder of this identity, NATS ACL can't independently distinguish ordinary vs admin-tier traffic — defense-in-depth for the P0 depends entirely on the (currently absent) HTTP-layer `require_operator` check. | Split admin-tier subjects onto a narrower, separately-reviewed ACL pattern (e.g. `factory.dashboard.admin.>`), or explicitly document that the `require_operator` fix (P0) is the sole control. |
| sec-dashboard-005 | `src/factory/dashboard/ops_proxy.py:64` | UNVERIFIED | `build_ops_log_query`/`_systemd_unit_for_container` interpolate the caller-supplied `container` query param directly into a LogQL stream selector via f-string with no escaping (only `.strip()`s, never escapes). `GET /api/bff/ops/logs` passes the raw param through with no auth dependency. A crafted value (e.g. embedded `"`) could break out of the `systemd_unit` matcher and query arbitrary Loki streams/job labels beyond the intended single-container scope, sent verbatim to Loki's `/loki/api/v1/query_range`. | Validate `container` against an allowlist of known `factory-*` systemd unit basenames (e.g. the set enumerated in `deploy/quadlet.toml`), rejecting `"`/`{`/`}`/whitespace; alternatively build the LogQL selector with proper label-value escaping instead of raw f-string interpolation; require operator auth on `/api/bff/ops/logs`. |
| sec-authz-003 | `src/factory/core/agent/agent_config.py:141` | UNVERIFIED | `AgentRow.permissions_json`/`Agent.permissions` is defined in schema, persisted, and threaded through `AssembleDeps` end-to-end, but a repo-wide grep for `.permissions` reads (excluding the column/skip_permissions refs) finds exactly one hit — the assignment itself; nothing ever reads it for enforcement. Tool restriction is actually driven by a separate mechanism (`tools_json` → `ModelConfig.tools` → `--allowedTools`). A security-named field that restricts nothing is a foot-gun for capability-scoping audits. | Either wire `permissions_json` into real enforcement (allow/deny list consumed before CLI subprocess spawn) or remove the column/field entirely (migration + doc update) so operators stop believing it restricts anything. |

### P3 — Low

| ID | File:line | Verdict | Evidence | Recommendation |
|---|---|---|---|---|
| sec-ingress-02 | `src/factory/ingress/orchestrator.py:107` | UNVERIFIED | `delivery_id = headers.get("X-GitHub-Delivery") or None` is GitHub-specific; Cloudflare webhook requests never carry this header, so `publisher.publish(lyra, msg_id=None)` always skips JetStream's `Nats-Msg-Id` dedup for Cloudflare events — a retried/replayed Cloudflare webhook within the HMAC-valid window is republished with no idempotency guard. | Derive a per-connector dedup key for Cloudflare (e.g. hash of body+timestamp header) and pass it as `msg_id`, or document as an accepted best-effort limitation if downstream consumers are already idempotent. |
| sec-blobstore-soul-04 | `src/factory/blobstore/auth.py:59` | UNVERIFIED | `BearerAuthMiddleware` authenticates with one process-wide shared token (`hmac.compare_digest`); storage layer has no per-agent/per-tenant scoping — any holder of the shared token (every same-host adapter + cross-host M2 workers) can GET/PUT/DELETE any blob by content hash, including another agent's `soul.md`. Matches the explicitly accepted single-trust-boundary design in ADR-090 ("logical agent tenancy inside one operator-owned deployment ... true multi-tenancy out of scope") — not a regression, but means blob-level isolation doesn't exist as a backstop, and the entire boundary rests on the (currently broken, see P0) BFF/RPC layer above it. | No action required if the single-operator/single-tenant threat model stays intentional (it does, per ADR-090's explicit scope note); re-confirm before any multi-operator/multi-tenant exposure is considered. |
| sec-authz-004 | `docs/architecture/target-architecture.md:42` | UNVERIFIED — **not a live bug, doc-only** | `GuardChain` was removed in PR #1997 (confirmed: zero hits anywhere in `src/factory/**/*.py`); `docs/architecture/security-routing.md` and `artifacts/frames/1982-...mdx` both document the removal and its hub-side replacement (`TrustGuardMiddleware` stage 3 + `AuthorizeAgentMiddleware` stage 7). Only `target-architecture.md`'s diagram is stale. | Safe to close as resolved; file a doc-only follow-up to regenerate the stale diagram. |

### Dedup notes

- 6 raw findings (`sec-authz-001`, `sec-secrets-acl-1`, `sec-blobstore-soul-01`, `sec-blobstore-soul-02`, `sec-dashboard-001`, `sec-dashboard-003`) collapsed into the single P0 row above — all describe the identical root cause (missing `Depends(require_operator)` on `bff_admin.py`/`bff_agents.py`), differing only in which consequence (admin grant, soul write, identity disclosure) the finder traced. Kept as one entry to avoid inflating the severity count; the merged row preserves every distinct failure-scenario branch from the 6 originals.
- `sec-secrets-acl-2` and `sec-secrets-acl-3` merged into one P2 row — same gate script (`check_str_exc_bus_bound.sh`), two independently-reproduced but mechanically distinct blind spots (regex syntax vs missing scan root), same remediation owner.
- `sec-dashboard-002` (platform-identity rebind/IDOR) and `sec-dashboard-004` (jobs/steer IDOR) are **not** merged into the P0 despite sharing its missing-auth root cause: both add a second, independent ownership-check gap that survives even after `require_operator` is wired in, so they're kept as distinct findings with their own remediation.

### Notes

- The P0 entry is the single most load-bearing finding in this domain: it was independently reached by all 5 finder agents from 4 different entry angles (admin-user authz, secrets/ACL, blobstore/soul, dashboard-general), and additionally self-corroborated by a contemporaneous internal audit pass already committed in this repo at this same path (commit `411ee09d` / PR #2070 lineage). Treat any cross-domain reduce step's "security" rollup as dominated by this one root cause, not 6 separate bugs.
- `require_operator` (`src/factory/dashboard/auth.py:37-51`) exists, is correctly used in `routes/connectors.py`, and even there fails open when `FACTORY_DASHBOARD_OPERATOR_TOKEN` is unset (default in `deploy/env/web.env.example`). Fixing the P0's missing `Depends()` calls is necessary but not sufficient — the token must also be made a required secret.
- Two distinct findings (sec-dashboard-002, sec-dashboard-004) show that even after the generic P0 auth fix lands, at least two routes (`admin user patch`, `jobs/steer`) need an additional **ownership/ACL check**, not just "is this caller an authenticated operator" — both are IDOR-shaped regardless of the auth gap.
- `tools/check_str_exc_bus_bound.sh` (ADR-073 SanitizedError discipline, #1212) has two independently-reproduced coverage gaps (regex syntax blind spot + missing scan roots) — same remediation owner, recommend fixing both in one pass.
- sec-authz-004 is the one finding in this domain confirmed **already resolved in code** (stale diagram only) — exclude from any "open security debt" rollup count beyond doc-drift.

### Debt subscore

`debt_subscore = 15/100`

Rationale: 2 CONFIRMED critical findings (one a self-corroborated, 6-way-converged unauthenticated admin/persona/grant-mutation surface; the other an unauthenticated cross-session text-injection IDOR), both with real, traced mutation power on hub-side stores and both currently reachable, net-new in this week's PR #2070/#1772/#1773 lineage. 2 further CONFIRMED high findings compound the same surface with additional logic gaps (identity-rebind IDOR, fully inert client-side secret lint). 6 medium and 3 low findings round out a domain with essentially no compensating control on the newest, highest-privilege subsystem shipped this cycle (no token enforced by default, no app-level auth middleware, no rate limiting, no ownership checks). Score sits in the bottom sixth rather than near-zero only because: blast radius is bounded to Tailnet membership (not public internet), the gap is freshly introduced (same-cycle, not a long-lived production exposure with confirmed exploitation), and one nominal P3 finding (`sec-authz-004`) is already resolved in code.


---

## SSoT domain — audit synthesis (2026-06-30)

24 raw findings (6 finders: `ssot-config`, `ssot-stores`, `ssot-nats`, `ssot-docs`, `ssot-ccc-sweep`, `ssot-crossrepo`) → **22 deduped** after merging one 3-way same-root-cause cluster (KV-bucket create-or-open idiom — see Duplication table). Ranked by `adjusted_severity` (fallback `severity`); ties broken CONFIRMED > PLAUSIBLE > UNVERIFIED.

### Severity counts (deduped)

| Severity | Count |
|---|---|
| critical | 1 |
| high | 3 |
| medium | 13 |
| low | 5 |

**debt_subscore: 40/100** (100 = pristine). Driven by one CONFIRMED critical (unauthenticated dashboard BFF admin/agent-grant routes — independently also flagged by the security domain sweep, see cross-domain note) and three CONFIRMED high doc/cross-repo SSoT-drift findings (deployment.md container-count, ADR-090 redirect-target staleness, a network-manifest host-role gap that would break fresh M₂ provisioning). The medium/low tail is mostly DRY/duplication (idempotent KV/stream provisioning, retry-backoff, secret-comparison, envelope-building reimplemented 3-7x with no shared helper) — real maintenance debt but lower urgency, and largely UNVERIFIED (not independently re-checked this pass).

---

### P0 — Critical

| # | File:line | Verdict | Title | Evidence (condensed) | Recommendation |
|---|---|---|---|---|---|
| 1 | `src/factory/dashboard/routes/bff_admin.py:24` + `src/factory/dashboard/routes/bff_agents.py:22` | CONFIRMED | Dashboard BFF admin/agents routes (this week's #1 feature) have zero operator-auth enforcement, unlike sibling BFF modules | `register_admin_routes()`/`register_agent_routes()` define POST/PATCH `/api/bff/admin/users`, GET `/admin/access`, POST/PATCH `/api/bff/agents`, PUT `/agents/{name}/soul` with **no** `Depends(require_operator)` anywhere in either file (verified by direct read, not just grep), and no app-level auth middleware exists in `app.py`. Sibling `connectors.py` gates every route via `Depends(require_operator)` — proving this is the established, deviated-from pattern. Corroborating: commit `06422bfd` (same day) added a client-side `operatorAuthHeaders()` that sends `Authorization: Bearer <token>` to these exact endpoints, but the backend handlers never read/validate that header — it is silently dropped. An independent security-domain finding (`sec-dashboard-1`) reaches the identical conclusion via the same file:line citations. Any network caller reaching the dashboard BFF can create/patch admin users with arbitrary agent grants and mutate agent config/soul with no credential at all. | Add `Depends(require_operator)` to every route in `bff_admin.py`/`bff_agents.py`, mirroring `connectors.py`. Treat as a current-deployment security incident: audit whether `FACTORY_DASHBOARD_OPERATOR_TOKEN` is set in prod and whether these endpoints have been externally reachable since PR #2070 merged. |

---

### P1 — High

| # | File:line | Verdict | Title | Evidence (condensed) | Recommendation |
|---|---|---|---|---|---|
| 2 | `docs/architecture/deployment.md:7` | CONFIRMED | deployment.md describes a 9-container topology; actual prod is 20 quadlet units / 22 processes | Line 7 claims "nine Quadlet containers"; table (lines 21-32) lists only 9. `find deploy/quadlet -name '*.container'` returns 20 files; `CURRENT.generated.md`'s auto-generated Process Topology lists 22 named processes including `factory-dashboard`, `factory-socialmedia-adapter`, `factory-ingress`, `factory-cloudflared`, and the 8-unit observability/Langfuse stack — none mentioned anywhere in the 253-line deployment.md. Header says "Last updated: 2026-06-17"; the missing services were added 2026-06-26 through 2026-06-30 and the file's one subsequent edit (2026-06-28) never touched the topology section. No disclaimer or superseding-doc pointer exists. | Regenerate the container table/topology diagram from `CURRENT.generated.md`'s Process Topology section so the domain page stays consistent with the generated SSoT; bump "Last updated." |
| 3 | `docs/architecture/security-routing.md:87` | CONFIRMED | security-routing.md describes the shipped `AuthorizeAgentMiddleware` (ADR-090) as merely planned/future work | Line 87: "ADR-090's **planned** agent-scoped authorization middleware ... is the **next** authorization stage." In reality `AuthorizeAgentMiddleware` is implemented (`middleware_authz.py:42`), wired live into the production pipeline (`middleware.py:206`), shipped in commit `a2c4680c` (2026-06-24), and covered by a 535-line test file. ADR-090 itself (status: accepted) explicitly designates this exact page as "Current truth" — so anyone following the documented cross-reference chain lands on stale, incorrect information about a shipped, production-enforced authorization stage. Doc dated "Last updated: 2026-05-09," over a month stale relative to the shipped feature. | Update security-routing.md to document `AuthorizeAgentMiddleware` as shipped (location, behavior, test coverage); remove "planned/next stage" language — this is the page ADR-090's own banner points readers to as current truth. |
| 4 | `roxabi-factory/deploy/quadlet.toml:167` | CONFIRMED | `network.roxabi` Quadlet manifest entry is scoped to `host_roles=["factory-hub"]` only, but llmCLI/imageCLI components requiring `Network=roxabi.network` run on `llm-worker`/`image-worker` hosts that don't carry `factory-hub` | Re-ran the actual cluster planner: `roxabitower roxabi-factory network roxabi roxabi.network no` while the same run shows `roxabitower imageCLI component gen imagecli-gen yes` and `roxabitower llmCLI component proxy llmcli yes` — both consumers planned on a host the network itself is never planned for. The project's own deployment standard documents `host_roles = ["any"]` as the idiom for "every host" but `network.roxabi` doesn't use it. A fresh M₂-role host following the documented onboarding flow (`~/projects/deploy.sh` alone) would fail to start `llmcli`/`imagecli-gen` for lack of the network; currently masked on the live box by a manually-placed `roxabi.network` unit outside the planner. llmCLI's `install.sh` has a partial documented fail-fast/remediation block for this; imageCLI has **none** — the gap is genuinely unmitigated there. | Change `[network.roxabi].host_roles` from `["factory-hub"]` to `["any"]` in `roxabi-factory/deploy/quadlet.toml`; re-run `deploy.sh --plan` to confirm parity, then verify on a fresh M₂-role host. |

---

### P2 — Medium

| # | File:line | Verdict | Title | Recommendation |
|---|---|---|---|---|
| 5 | `src/factory/infrastructure/stores/base/message_index.py:34` | CONFIRMED | Dead SQLite `MessageIndex` store duplicates the live `MessageIndexKvStore` interface post-#1059 NATS-KV migration — identical method surface, zero production callers (only tests), still exported from `stores/__init__.py.__all__` | Remove or clearly mark `base/message_index.py` deprecated/legacy-test-only; drop it from the public `__all__` so it can't be accidentally wired back in over the canonical KV store. |
| 6 | `docs/claude-md-registry.md:5` | CONFIRMED | Registry (31 rows) is missing 3 active AGENTS.md instruction files added this sprint — `src/factory/dashboard/AGENTS.md`, `packages/roxabi-obs/AGENTS.md`, `packages/roxabi-satellite/AGENTS.md` — none have a CLAUDE.md shim or parent fallback, so their content (incl. dashboard import-boundary rules) is never auto-loaded by Claude Code | Add the 3 missing rows per the registry's own "Update here on add/rename/delete" rule; create the missing CLAUDE.md shims. Note: the two cited import-boundary "MUST NOT" rules for dashboard ARE independently CI-enforced via `.importlinter` contracts, which is why this was downgraded from high — but `roxabi-obs`'s "no factory.* imports" rule and session-ID-taxonomy guidance are not covered by any gate and remain genuinely unenforced/unsurfaced. |
| 7 | `deploy/install.sh:403` | CONFIRMED | Final operator-restart hint hand-enumerates 8 of 22 quadlet.toml components (omits `factory-dashboard`, `factory-omp`, `factory-ingress`, `factory-socialmedia-adapter`, the whole observability/Langfuse stack) and has been unmaintained since `factory-blobstore` was added, while every sibling restart/verify call site (Makefile, `converge.sh`, `quadlet-install-verify.sh`) was already refactored to derive the unit list dynamically via `quadlet_containers()` per `#1988` | Source `deploy/lib/quadlet-units.sh` in `install.sh` and replace the static string with a dynamic `quadlet_containers()`-derived list, matching the pattern already used elsewhere. Downgraded from high: informational-only hint on a rare manual (non-`make converge`) path, immediately followed by `systemctl --user status 'factory-*'` in the runbook, which would surface the gap quickly. |
| 8 | `deploy/quadlet.toml:3` | UNVERIFIED | `[meta].uid = 1500` is presented as canonical but is structurally dead — no tool reads it; the real UID is hand-duplicated 31x as literals across 10 unit files (`UserNS=`, `Secret=...,uid=1500,gid=1500`) with no gate enforcing agreement | Delete the unused `[meta].uid` field, or add a gate parsing it and cross-checking every unit file literal against it. |
| 9 | `scripts/check_grants.py:30` | UNVERIFIED | `check_grants.py`'s `RESOURCES` list is a third hand-authored copy of acl-matrix.json's audio-consumer group subjects — introduced by #1577 to replace `code-subjects.json` but never actually derived from the matrix, so a subject added/removed from the group silently stops being validated rather than flagging drift | Derive `RESOURCES`' subject lists directly from `matrix['groups'][name]` instead of hand-listing them. |
| 10 | `src/factory/nats/AGENTS.md:54` | UNVERIFIED | Stale subject name `factory.llm.health.{worker_id}` restated in 2 docs (also `706-per-role-nkeys-acls-spec.mdx:117`, outside the generated sentinel block); real subject is `factory.llm.heartbeat` per `acl-matrix.json` and the `roxabi-contracts` canonical declaration — wrong since before the lyra→factory rebrand, outside any CI-checked surface | Fix both restatements to `factory.llm.heartbeat`; consider moving the footnote inside the generated sentinel block so this class of drift fails CI. |
| 11 | `docs/architecture/workers-tooling.md:56` | UNVERIFIED | Method-dumps a full `ComplexityEstimator`/`ComplexityLevel`/`COMPLEXITY_TO_MODEL` class that does not exist anywhere in `src/`/`tests/`/`packages/`; `security-routing.md:151` repeats the same fictional `SmartRoutingDecorator` claim | Remove or clearly mark as an unimplemented design proposal — it currently reads as a pasted excerpt of real shipped code. |
| 12 | `AGENTS.md:86` | UNVERIFIED | "20 containers" enumeration is stale — omits `factory-ingress`/`factory-cloudflared` added 2026-06-30; last reconciled 2026-06-27. Same underlying root cause as #2/#7 (no single generated source for the container list) | Update the enumeration, or replace with a pointer to `CURRENT.generated.md`'s Process Topology to avoid future drift (see Duplication table). |
| 13 | `src/factory/infrastructure/turn_writer/stream_setup.py:71` | UNVERIFIED | "add_stream / on-conflict update_stream" idempotent-provisioning pattern copy-pasted verbatim (structurally identical try/except scaffold) between `turn_writer/stream_setup.py` and `outbound_audio/stream_setup.py` — the latter's own docstring self-documents the duplication | Factor the shared try/except scaffold into one `ensure_jetstream_stream(js, cfg, *, label)` helper so future stream additions reuse it instead of copy-pasting. |
| 14 | `src/factory/adapters/shared/_shared.py:121` | UNVERIFIED | "Retry with backoff" loop hand-rolled independently 4x (`llm/decorators.py`, `core/hub/outbound/_dispatch.py`, `adapters/shared/_shared.py`, `nats/audio_publish.py`) — each with its own attempt count, delay formula, and exception classification; no shared primitive | If the differing semantics are intentional, factor out a shared parametrized retry primitive (attempts, delay sequence, retryable-exception predicate, on-exhaustion behavior). |
| 15 | `src/factory/dashboard/auth.py:49` | UNVERIFIED | Bearer-token/shared-secret comparison via `hmac.compare_digest` reimplemented in 6 non-shared modules (`ingress/verify.py`, `telegram_guard.py`, `blobstore/auth.py`, `bootstrap/infra/health.py`, `dashboard/auth.py`, `dashboard/stream_tokens.py`) with no common primitive — explicitly the kind of duplication that let finding #1 happen (2 new route modules simply never wired in any existing pattern) | Extract a shared `verify_shared_secret(presented, expected) -> bool` (or FastAPI dependency factory) so every new authenticated surface reuses it instead of re-deriving the comparison. |
| 16 | `src/factory/nats/nats_channel_proxy.py:126` | UNVERIFIED | Outbound NATS envelope dict (`{"type":..., "stream_id":...}` + `json.dumps().encode()`) hand-built 7x across `nats/` and `adapters/nats/` with per-type field shape as implicit convention rather than an enforced shared builder | Introduce a small `build_outbound_envelope(type, stream_id, **fields) -> bytes` helper so envelope-shape changes touch one place instead of 7. |
| 17 | `voiceCLI/deploy/secrets-policy.toml:1` | UNVERIFIED | `secrets-policy.toml` is documented as a cross-repo SSoT registry but is only actually enforced (parsed by `install.sh`) in roxabi-factory; voiceCLI/llmCLI ship it decoratively, imageCLI doesn't have it at all; no CI in any repo verifies the file matches what `install.sh` actually creates | Either make the file load-bearing everywhere (each repo's `install.sh` parses its own copy) or drop the unenforced reference from voiceCLI/llmCLI rather than presenting documentation as a real registry. |

---

### P3 — Low

| # | File:line | Verdict | Title | Recommendation |
|---|---|---|---|---|
| 18 | `src/factory/blobstore/serve.py:61` (+4 siblings) | CONFIRMED | "Create-or-open JetStream KV bucket" idiom reimplemented 5x (`infrastructure/kv/factory_state.py`, `stores/jobs/active_jobs_kv.py`, `outbound_audio/stream_setup.py`, `stores/kv/message_index_kv.py`, `blobstore/serve.py`) with divergent race-handling robustness — `blobstore/serve.py`'s copy has no inner-except fallback on the `create_key_value()` race at all, unlike its 4 siblings. *(merged: includes `ssot-stores-1`'s narrower 2-module variant and `ssot-stores-3`'s 4-module variant — see Duplication table)* | Extract one `ensure_kv_bucket(js, KeyValueConfig) -> KeyValue` helper; all 5 (6 incl. `roxabi-nats`) call sites delegate to it. Downgraded from high/medium: the affected `blobstore.ready` flag has zero readers in `src/` today (forward-looking instrumentation for an unimplemented reconciler, #1068), the race window only exists on true cold-bootstrap of the bucket, the gap is logged not silent, and `factory-hub.service` `After=factory-blobstore.service` narrows the window further. |
| 19 | `src/factory/infrastructure/turn_writer/stream_setup.py:37` | UNVERIFIED | `FACTORY_TURNS` stream name hardcoded in 3 places (`stream_setup.py`, `acl-matrix.json`, `check_grants.py`) with no `roxabi-contracts` constant, unlike `FACTORY_OUTBOUND_AUDIO` which already has `STREAM_AUDIO` centralized for the analogous stream | Add a `STREAM_TURNS` constant to roxabi-contracts mirroring `STREAM_AUDIO`; import it at all 3 sites. |
| 20 | `src/factory/infrastructure/stores/identity/user_store.py:58` | UNVERIFIED | `_utc_now()` one-liner reimplemented byte-for-byte identical 4x (`user_store.py`, `auth_store.py`, `identity_alias_store.py`, `core/stores/pairing_config.py`) — a sibling file (`pairing.py`) already imports the shared version rather than redefining it | Move `_utc_now()` into a shared module and import everywhere instead of redefining; low risk/effort. |
| 21 | `docs/architecture/adr/094-control-plane-dashboard-consolidation.mdx:8` | UNVERIFIED | ADR-084 and ADR-094 (this week's dashboard-consolidation decision) are the only 2 of 72 top-level ADRs (besides an intentionally-different ADR-042) missing the standard "Current truth" redirect banner; ADR-094 is compounded by there being no dedicated dashboard domain page to redirect to | Add "Current truth" banners to both; consider a dedicated `docs/architecture/dashboard.md` domain page given the subsystem's size this sprint. |
| 22 | `voiceCLI/deploy/install.sh:82` | UNVERIFIED | `deploy/install.sh` is copy-pasted-and-diverged across all 4 repos (voiceCLI/llmCLI/imageCLI/factory) — inconsistent boolean idioms (`DRY_RUN=0`/`-eq 1` vs `DRY_RUN=false`/`if "$DRY_RUN"`), a literal duplicate `mkdir -p` line in voiceCLI, no shared bash lib despite factory already having its own `deploy/lib/deploy-common.sh` (used only by factory) | Extract truly common primitives (dry-run wrapper, idempotent secret-create, daemon-reload) into a shared `~/projects/lib/install-common.sh`; remove the duplicate mkdir line as a quick win. |

---

### Duplication / cross-cutting table

| Cluster | Findings merged/related | Root cause | Note for reduce step |
|---|---|---|---|
| KV bucket create-or-open idiom | `ssot-ccc-sweep-003` (kept as #18, 5-module version, CONFIRMED) ⊃ `ssot-stores-1` (2-module variant, CONFIRMED — `factory_state.py` vs `roxabi-nats/readiness.py` divergent `err_code==10058` handling) ⊃ `ssot-stores-3` (4-module variant, UNVERIFIED) | Same try-create→`BadRequestError`/`BucketNotFoundError`→bind control flow hand-copied per bucket across `infrastructure/kv`, `outbound_audio`, `stores/jobs`, `stores/kv`, `blobstore`, plus a 6th copy in the separate `roxabi-nats` package (can't import `factory.*`) | One shared helper closes 5-6 sites; flag for any other domain's "infra duplication" rollup |
| Container/component enumeration drift (3 independent surfaces, not literally merged — different files/audiences) | `ssot-config-1` (#7, install.sh hint) + `ssot-docs-1` (#2, deployment.md) + `ssot-docs-5` (#12, AGENTS.md) | No single generated source of truth for "how many quadlet containers exist" — three independently hand-maintained enumerations have each drifted as `deploy/quadlet.toml` grew from 9→20-22 components; `CURRENT.generated.md`'s Process Topology already IS the generated SSoT but nothing points the other 3 docs/scripts at it | Recommend a single fix-pattern (derive from `CURRENT.generated.md`/`quadlet_containers()`) applied across all 3, rather than 3 separate doc edits |
| Dashboard operator-auth: missing gate + missing shared primitive | `ssot-ccc-sweep-001` (#1, critical — the gate is simply absent on 2 new route modules) + `ssot-ccc-sweep-006` (#15, medium — 6 independent hand-rolled `hmac.compare_digest` call sites, no shared `verify_shared_secret` helper) | `ssot-ccc-sweep-006`'s own evidence explicitly states this duplication "is exactly the kind ... that let finding #1 happen" — absent a shared primitive, a new route module has nothing standard to wire in | Fixing #1 (add `Depends(require_operator)`) is the immediate fix; #15 is the structural prevention for the *next* occurrence — keep both, don't conflate severities |
| Idempotent JetStream provisioning, broader family | KV-bucket cluster (#18) + `ssot-stores-4` (#13, add_stream/update_stream pattern) | Same general "ensure resource exists, race-tolerant" shape (KV buckets vs streams) reimplemented per-module rather than via one provisioning helper in `infrastructure/` or `roxabi-nats` | Could be solved by one generic `ensure_jetstream_resource()` abstraction if a future refactor touches this area; not merged here since buckets vs streams are different JetStream APIs |
| Cross-repo deploy/install convention divergence | `ssot-crossrepo-4` (#17, secrets-policy.toml decorative outside factory) + `ssot-crossrepo-5` (#22, install.sh diverged 4 ways) | `~/projects` has no shared deploy/install library; each of factory/voiceCLI/llmCLI/imageCLI independently reimplements dry-run flags, secret-create loops, and the secrets-policy.toml convention, with visible drift (boolean conventions, enforcement gap) already present | Not literal duplicates (different artifacts: bash boilerplate vs TOML registry) but one underlying gap — `~/projects/lib/install-common.sh` does not exist; worth one cross-repo epic |
| **Cross-domain**: dashboard BFF auth gap | `ssot-ccc-sweep-001` (#1, this domain) ≈ `sec-dashboard-1` (security domain, per its own verify_reasoning trace into `admin_rpc.py`) | Same code defect (`bff_admin.py`/`bff_agents.py` missing `Depends(require_operator)`), independently surfaced by the SSoT sweep (as a "missing the established pattern" duplication signal) and the security domain (as a privilege-escalation vulnerability) | **Reduce step: merge into ONE P0 item across domains** — do not double-count severity/debt impact between `ssot.md` and `security.md` |



---

## Domain audit: deploy (roxabi-factory + cluster deploy tooling)

Source: 7 findings (3 finders: `deploy-factory`, `deploy-cluster`, `deploy-acl-pipeline`) — REFUTED findings already excluded upstream. Verdicts/severities below are the post-verify-pass values (`verdict` + `adjusted_severity` where present; UNVERIFIED findings carry their original `severity` unchanged, not independently re-checked in this pass).

Note: an earlier draft of this file (different finding-ID set: `NO_RESTART=1`, `managed_repos` allowlist, etc.) previously occupied this path from a separate finder pass on the same domain. This write supersedes it with the 7-finding set supplied for this synthesis run.

### Dedup notes

- `deploy-cluster-quadlet-containers-role-blind` is flagged by its own evidence (`ssot_duplicate_of: lib/cluster_plan.py:roles_match`) as **related-but-distinct** from `deploy-cluster-role-rename-orphan`. Not merged: different file (`roxabi-factory/deploy/lib/quadlet-units.sh` vs `lib/cluster_plan.py`/`deploy.sh`) and different failure mechanism (client-restart fan-out list vs. unattended `--prune` deletion). Both stem from the same root cause — **no single canonical role-validation SSoT enforced across `hosts.toml`, every managed repo's `deploy/quadlet.toml`, and factory's own ad-hoc shell helpers** — and should be fixed together. Kept as two rows, cross-referenced.
- `deploy-factory-01` (Makefile `quadlet-install` blindly re-copies the static unit fleet `deploy.sh` already installed) and `deploy-cluster-quadlet-containers-role-blind` (factory's `quadlet-units.sh` re-enumerates units independently of `cluster_plan.py`) share a weaker family resemblance — both are cases of roxabi-factory's local deploy tooling duplicating logic that `~/projects/deploy.sh` / `cluster_plan.py` already own as SSoT — but touch different files/recipes and have different blast radii (install-time copy churn vs. restart-list enumeration). Not merged; noted for cross-domain awareness since the underlying anti-pattern (factory-local shell re-deriving cluster-level state) recurs.
- No other exact file:line overlaps among the 7 findings; each addresses a distinct mechanism (Makefile install duplication, missing `RestartForceExitStatus=`, langfuse `RestartSec` drift, role-rename orphan/prune, role-blind restart list, ACL grant gap ×ADAPTER, ACL fixture pre-push enforcement gap).

### Ranking (adjusted_severity desc, ties broken by verdict: CONFIRMED > PLAUSIBLE > UNVERIFIED)

#### P0 — Critical

| # | File:line | Verdict | Title |
|---|---|---|---|
| 1 | `lib/cluster_plan.py:45` (projects-meta) | CONFIRMED | hosts.toml role renames desync silently from per-repo `quadlet.toml` `host_roles`, causing unattended `--prune` to delete live production units |
| 2 | `deploy/nats/acl-matrix.json:94` | CONFIRMED | telegram-adapter / discord-adapter missing `$JS.API.STREAM.NAMES` + `CONSUMER.INFO` grants for `wait_for_hub()` `kv.watch()` fallback — same bug that crash-looped the dashboard same-day, never backported |

#### P1 — High

_(none — no finding retained high severity after verification)_

#### P2 — Medium

| # | File:line | Verdict | Title |
|---|---|---|---|
| 3 | `deploy/quadlet` (all 22 `.container`/`.container.tmpl` units) | UNVERIFIED | S12's `RestartForceExitStatus=` (fatal-config exit-code carve-out) absent from every factory Quadlet unit, unlike voiceCLI/llmCLI siblings |
| 4 | `roxabi-factory/deploy/lib/quadlet-units.sh:12` | UNVERIFIED | converge.sh's client-restart unit list (`quadlet_containers`) bypasses `cluster_plan.py`'s host-role SSoT entirely |
| 5 | `.pre-commit-config.yaml:117` / `tests/scripts/fixtures/v3-pre-grant-group.json` | UNVERIFIED | Hand-mirrored ACL parity fixture has zero local pre-push enforcement — only the full CI pytest run catches a stale mirror |

#### P3 — Low

| # | File:line | Verdict | Title |
|---|---|---|---|
| 6 | `Makefile:165` | CONFIRMED (severity downgraded high→low on verify) | `make quadlet-install` (converge step 4) blindly re-installs the entire static unit fleet, contradicting its documented division of labor with `deploy.sh` |
| 7 | `deploy/quadlet/factory-langfuse-web.container:22` | UNVERIFIED | `factory-langfuse-web`/`-worker` deviate from S12's `RestartSec=10` with no documented rationale |

---

### Detail

#### 1. [P0] hosts.toml role-rename desync → unattended `--prune` deletes live units — `lib/cluster_plan.py:45-48`, `184-190`

- **Verdict:** CONFIRMED, critical (kept)
- **Evidence:** `roles_match()` (`cluster_plan.py:45-48`) is a pure set-intersection with no validation against any canonical role registry; `hosts.toml`'s role catalogue (`hosts.toml:33-38`) is explicitly labeled "informatif" (unenforced comment). A role-mismatched component is logged as `# SKIP` to stderr only (`cluster_plan.py:184-190`) — never counted in `errors`/`soft_errors`. `deploy.sh`'s orphan-prune (`deploy.sh:418-468`) only gates on `errors==0`, so a role-desynced-but-previously-installed unit is auto-`rm -f`'d on the very next unattended run. `roxabi-factory/deploy/converge.sh` runs `deploy.sh --prune` every ~5 minutes via `factory-quadlet-sync.timer`/`factory-post-autoupdate.timer`, with no preview step. This exact fault class already happened in production: commit `c35721b`/#1801 ("rename M1 role `lyra-hub`→`factory-hub`") describes the identical mechanism stalling the entire factory stack on M1 because `hosts.toml` (in the separate, manually-pulled `projects-meta` repo) drifted from `roxabi-factory`'s auto-synced `quadlet.toml`. No CI runs `cluster_plan.py self-test` for `projects-meta` (no `.github/` dir exists there), and the existing self-test only hardcodes today's known-good role names, so it would not catch a *new* rename.
- **Mitigating factor found on verify:** `roxabi-factory/deploy/converge.sh:17` (`require_host_role factory-hub`, added 2026-06-28 — *after* the #1801 incident) hard-exits before `deploy.sh --prune` runs if the *host's* role set doesn't contain `factory-hub`. This would block a repeat of the exact #1801 *host-level* rename scenario, but does nothing for (a) a single component's `host_roles` value drifting independently while the host's own role set stays correct, or (b) any host other than the one this one literal string covers. The general mechanism — role desync → silent SKIP → unattended prune of a live unit — remains fully live for every other case, and an independent precedent (llmCLI's xai/fw-forwarder orphaned the same way, fixed via llmCLI PR #113) confirms the narrower per-component path already bit production once.
- **Recommendation:** Add a `[roles]` table in `hosts.toml` as the single enumerated source of valid role strings (or a CI check diffing every managed repo's `quadlet.toml` `host_roles` against it), and wire `cluster_plan.py self-test` into real CI for `projects-meta` (currently none exists). Until then, treat an unmatched-but-previously-installed unit as a `soft_error` (blocking `--prune` for that file) instead of a silent orphan.

#### 2. [P0] telegram/discord-adapter missing ACL grant for `wait_for_hub()` cold-boot fallback — `deploy/nats/acl-matrix.json:94-150`

- **Verdict:** CONFIRMED, critical (kept)
- **Evidence:** `wait_for_hub()` (`packages/roxabi-nats/src/roxabi_nats/readiness.py:165-219`) tries `kv.get('hub.ready')` first; on `KeyNotFoundError` it falls through, **uncaught**, to `kv.watch('hub.ready')` (`readiness.py:128`, called before the function's own `try:` at line 129; `wait_for_hub()` itself has no surrounding try/except either). Traced 1:1 through nats-py: `KeyValue.watch()` → `subscribe()` with no explicit stream → `find_stream_name_by_subject()` → publishes `$JS.API.STREAM.NAMES`, then `watcher._sub.consumer_info()` needs `$JS.API.CONSUMER.INFO.<stream>.*`. Both telegram-adapter (`acl-matrix.json:102-113`) and discord-adapter (`:131-142`) grant `CONSUMER.CREATE.*`, `STREAM.INFO`, `STREAM.MSG.GET` but **not** `STREAM.NAMES` or `CONSUMER.INFO.*`; web-adapter has both. Both adapters call `wait_for_hub()` as an unguarded "load-bearing barrier" (`standalone_telegram.py:111`, `standalone_discord.py:143`) with no exception handling anywhere up to `factory/cli/main.py`'s bare `asyncio.run()`, so any timeout/denial crashes the process outright. **Proof this is live, not theoretical:** commit `95b566bc`, same day (2026-06-30), re-added exactly these two grants to web-adapter only, with message "Dashboard crash-looped on `$JS.API.STREAM.NAMES` and `CONSUMER.INFO` when `wait_for_hub` fell through to `kv.watch()`. Restores grants trimmed in #1727 while readiness.py still uses the watch path." telegram-adapter/discord-adapter — the two primary user-facing bot adapters, running the identical code path — were never backported. All `After=factory-hub.service` orderings (no `Requires=`) leave the cold-boot race window open on every restart.
- **Recommendation:** Add `$JS.API.STREAM.NAMES` and `$JS.API.CONSUMER.INFO.KV_factory-state.*` to telegram-adapter and discord-adapter's publish lists, regenerate the 4 derived ACL artifacts, hand-mirror the diff into `tests/scripts/fixtures/v3-pre-grant-group.json`. Longer-term: wrap the `kv.watch()` call itself (not just the iteration loop) in `readiness.py`'s try/except so a permissions violation degrades gracefully instead of crash-looping, and add the nats-server-backed integration test deferred to #716.

#### 3. [P2] `RestartForceExitStatus=` (S12 fatal-config carve-out) absent from all 22 factory Quadlet units — `deploy/quadlet`

- **Verdict:** UNVERIFIED (not independently re-checked in this pass; carried at original medium severity)
- **Evidence (as submitted):** `container-deployment-standard.md:39` (S12) documents `RestartForceExitStatus=` for exit codes 100-127 (fatal config, don't restart-loop). `grep -rn RestartForceExitStatus deploy/quadlet/*.container*` returns zero matches across all 22 factory units. Sibling repos already implement this: `voiceCLI/deploy/quadlet/voicecli-stt.container:44` (`RestartForceExitStatus=3 78`), `llmCLI/deploy/quadlet/llmcli.container:44` (`RestartForceExitStatus=1 127`). `deploy/AGENTS.md` calls factory "the reference implementation for the Roxabi Quadlet pattern," yet it is the one repo missing this half of its own documented standard.
- **Recommendation:** Identify each container's fatal vs. transient exit codes (mirroring voiceCLI/llmCLI) and add `RestartForceExitStatus=` to every `factory-*.container` `[Service]` block, with a one-line comment documenting the chosen codes.

#### 4. [P2] `quadlet_containers()` client-restart list bypasses `cluster_plan.py`'s role SSoT — `roxabi-factory/deploy/lib/quadlet-units.sh:12-20`

- **Verdict:** UNVERIFIED (not independently re-checked in this pass; carried at original medium severity)
- **Evidence (as submitted):** `quadlet_containers()` parses every `container = "X.container"` entry out of `deploy/quadlet.toml` with a plain grep and applies no `host_roles` filtering — a second, independent enumeration of "units that exist" alongside `cluster_plan.py`'s role-aware `roles_match()`/`scan_manifests()`. `converge.sh:117` (`mapfile -t _all_svcs < <(quadlet_containers)`) feeds this unscoped list straight into the structural-drift client-restart fan-out (`converge.sh:126-129`). Consistent today only because every `[component.*]` in `roxabi-factory/deploy/quadlet.toml` declares the identical `host_roles=["factory-hub"]` (verified across all 17 component blocks) — nothing enforces that invariant going forward.
- **Recommendation:** Derive the restart list from `cluster_plan.py`'s role-aware install-plan (single SSoT) instead of re-deriving it, or add an explicit assertion documenting the "all factory components share `host_roles=[factory-hub]`" invariant this script silently depends on. Cross-ref finding #1 (same root cause: no canonical role SSoT enforcement).

#### 5. [P2] ACL parity fixture has zero local pre-push enforcement — `.pre-commit-config.yaml:117`, `tests/scripts/fixtures/v3-pre-grant-group.json`

- **Verdict:** UNVERIFIED (not independently re-checked in this pass; carried at original medium severity — finder's own notes already downgrade the worst-case framing: CI gates merge, so this is a dev-friction cost, not a deploy-correctness/security risk)
- **Evidence (as submitted):** `deploy/nats/acl-matrix.json` is documented SSoT driving 4 regenerated artifacts; 3 of them (706-spec, `v3-current.json`, `auth.conf`, `CURRENT.generated.md`) are covered by pre-push hooks (`acl-specs-drift`, `acl-authconf-drift`, `architecture-snapshot`). None of the 10 pre-push hooks invoke pytest or reference `v3-pre-grant-group.json` (confirmed by grep across the whole pre-commit config per the finder). The sole check comparing `acl-matrix.json` against this hand-maintained fixture is `tests/scripts/test_renderer.py::TestGrantGroupEquality::test_v4_render_set_equals_v3_render`, which runs only as part of the full CI `pytest tests/` invocation (`.github/workflows/ci.yml:188`). `make hooks-install` wires `pre-commit install --hook-type pre-push` only; `make test` is a separate, never-auto-wired target; no single "regen everything" Makefile target covers all 4 artifacts + the fixture.
- **Recommendation:** Add a pre-push hook running `pytest tests/scripts/test_renderer.py -k TestGrantGroupEquality` so the hand-mirror requirement is enforced locally before push, not only in CI. Document the hand-mirror step in `CONTRIBUTING.md`/the ACL-change runbook.

#### 6. [P3] `make quadlet-install` blindly re-installs the static unit fleet `deploy.sh` already installed — `Makefile:165-191`

- **Verdict:** CONFIRMED, **severity downgraded high→low on independent verify**
- **Evidence:** `Makefile:168`'s `rm -f` lists the same glob `"$(QUADLET_DIR)"/factory*.{network,volume,container,pod}` twice (literal copy-paste bug), and `Makefile:170-172` unconditionally re-`cp`'s every static unit with **no `cmp -s` idempotency gate**, unlike `~/projects/deploy.sh`'s INSTALL (`deploy.sh:229`) and AUXCOPY (`deploy.sh:360`) actions, which both skip the copy when content is unchanged. `deploy/converge.sh` step 3 (`deploy.sh --prune`, the new role-aware cluster SSoT) already installs that exact same static-unit fleet one step before `make quadlet-install` (step 4) redundantly re-touches it. Git history shows this is leftover from a 2026-06-28 migration (commit `229238f0` added the `deploy.sh --prune` step and documented the new step3/step4 split in `deploy/AGENTS.md:129-130`) that never trimmed the Makefile recipe to match.
- **Why downgraded:** Today the duplication is byte-identical and harmless — wasted I/O/mtime churn only. The original "high" framing hinged on the S20 `bot_secrets_fragment` splice pattern being live elsewhere in the cluster and at risk of being silently overwritten by this recipe; on verify, that pattern has **zero live usage** anywhere across all 4 managed repos (a misread of the standards doc's only worked example, which was factory's own now-deleted implementation), and factory explicitly migrated away from fragment-splicing to a DB-driven `tools/render_quadlet.py` RENDER-action mechanism for the only components that ever carried bot secrets (telegram/discord), with `tools/check_quadlet_template_purity.sh` (wired into `make quadlet-lint`) now permanently banning splice blocks and `Secret=factory-bot-*` lines in tracked `.container.tmpl` files, including a regression test for this exact reintroduction. The component class this finding worried about is structurally immune today.
- **Recommendation:** Narrow the Makefile `quadlet-install` recipe to only render/copy the two templated units (telegram, discord) plus `factory bot init`, dropping the blanket rm+cp of every static unit (already `deploy.sh`'s job). If a from-scratch bootstrap path still needs the full-fleet copy, gate it with the same `cmp -s` check `deploy.sh` uses. Fix the duplicated glob clause on `Makefile:168`.

#### 7. [P3] `factory-langfuse-{web,worker}` RestartSec deviation — `deploy/quadlet/factory-langfuse-web.container:22`

- **Verdict:** UNVERIFIED (not independently re-checked in this pass; carried at original low severity)
- **Evidence (as submitted):** `RestartSec=15` vs. S12's standard `RestartSec=10`, used correctly by all other 20 factory units including the 4 sibling langfuse dependency units (postgres/clickhouse/redis/minio). No comment, exemption note, or ADR carve-out — the documented ADR-092 hardening exemption for these units covers `NoNewPrivileges`/`ReadOnly`/`DropCapability` only, not `RestartSec`. Introduced in commit `1d3a0d05` and unchanged since.
- **Recommendation:** Either change `RestartSec` to 10 on both units to match S12, or add a one-line comment justifying 15s (e.g., DB-dependent slower startup) so the deviation reads as intentional rather than copy-paste drift.

---

### Debt subscore: 33/100

Driven by 2 independently-CONFIRMED critical findings — one with same-day production precedent (`acl-pipeline-1`: dashboard literally crash-looped on this exact code path, fix not backported to the two primary bot adapters) and one with a dated production-incident history plus a newly-widened blast radius via unattended `--prune` (`cluster-role-rename-orphan`, partially but not fully mitigated by a 2026-06-28 host-level gate) — plus 3 UNVERIFIED mediums (missing `RestartForceExitStatus=` fleet-wide, a second role-SSoT-bypassing restart-list enumeration, and an ACL-fixture pre-push gap) and 2 low findings (one confirmed-but-downgraded Makefile duplication bug, one unverified `RestartSec` cosmetic drift). The two P0s alone — both already-manifested-or-precedented production-availability/data-loss mechanisms on the cluster's deploy path — anchor this well below the domain midpoint.


---

## week-subsystem domain — audit synthesis (2026-06-30)

Scope: code shipped THIS WEEK across 4 sub-areas, one finder per area — `week-ingress` (factory-ingress webhook tenant registry), `week-fleet-obs` (fleet container observability/reporter), `week-soul` (persona/soul system-prompt + OMP V2), `week-dashboard-ux` (dashboard chat cockpit + agents config UI). 17 findings ingested (REFUTED already removed upstream). No two findings collapse to the identical file:line region — each sub-area was built independently this week, so overlap is thematic/pattern-level rather than literal duplication (see Duplication / cross-cutting notes at bottom).

Ranking: adjusted_severity desc, then verdict (CONFIRMED > PLAUSIBLE > UNVERIFIED).

### P0 — Critical (1)

| # | File:Line | Title | Verdict | Evidence | Recommendation |
|---|---|---|---|---|---|
| 1 | `src/factory/adapters/omp/omp_pool.py:64` | OMP V2 system-prompt apply calls `set_system_prompt`, a method that does not exist on the real `omp_rpc.RpcClient` — persona/soul is silently never applied to any OMP-backed agent | CONFIRMED | `_apply_omp_system_prompt()` does `getattr(client, "set_system_prompt", None); if callable(setter): setter(...)`. Independently confirmed the real vendored `omp_rpc.RpcClient` (pinned commit `4b5200a16...`, matching `Dockerfile:143`, cross-checked against 3 independent copies incl. upstream wire-protocol `rpc-types.ts`) has no such method and no such RPC command exists at all — the only system-prompt mechanism is constructor-time `append_system_prompt`, never passed at either `RpcClient(...)` call site in `_start_worker()`. Call is a guaranteed silent no-op: `getattr` returns `None`, `callable(None)` is `False`, nothing happens — no exception, no warning, only a misleading debug log. Shipped this week in commit `9805d436` ("OMP V2 applies soul at session boundary"), part of the persona-soul-blobstore epic (PR #2068, merged just before this audit). Pool bookkeeping (`worker.system_prompt = system_prompt`, omp_pool.py:141-168) records the prompt as "applied" regardless, masking the failure from any downstream consumer. Both tests covering this path (`tests/llm/drivers/test_omp_pool.py:341`, `tests/core/test_soul_harness_parity.py:52,82`) manually monkeypatch `client.set_system_prompt = MagicMock()` on a bare, unspec'd `MagicMock()` — CI structurally cannot catch the drift since no test ever specs against the real `omp_rpc.RpcClient` API. Reachable on every regular chat turn and dashboard "jobs launch" routed to an OMP-backed agent. | (a) Respawn the OMP subprocess with `append_system_prompt=system_prompt` on prompt change (mirrors CliPool's respawn-on-change contract), or (b) upstream a real RPC-level setter in `omp_rpc` and gate on its presence with a loud failure until confirmed available. Add a CI test that constructs `MagicMock(spec=omp_rpc.RpcClient)` (or imports the real pinned client) so a missing attribute fails loudly instead of silently. Until fixed: every OMP-backed agent runs with no persona/system prompt in production. |

### P1 — High (5)

| # | File:Line | Title | Verdict | Evidence | Recommendation |
|---|---|---|---|---|---|
| 2 | `packages/roxabi-obs/src/roxabi_obs/reporter.py:82` | `FleetReporter`'s try/except doesn't cover report construction — a crashed reporter can now take down the entire hub | CONFIRMED | `_publish_once()` builds the report via `new_container_report(...)` + `.model_dump_json()` OUTSIDE the try block that only wraps `nc.publish(...)`. `ContainerReport.container_name` has a pydantic `field_validator` that raises `ValueError` for any char outside `[A-Za-z0-9_-]` — reproducible, not theoretical (externally-supplied `CONTAINER_NAME` env var). `hub_standalone.py:292-307` appends `fleet_reporter_task` to the hub's `tasks` list passed into `_run_shutdown` → `watchdog()` (`utils.py:29-78`), which treats ANY task exception as fatal and calls `stop.set()`, tearing down the whole hub. Current Quadlet `CONTAINER_NAME` values are all valid today (latent, not active, risk), but a single bad value in a future Quadlet edit crash-loops the hub. The `# noqa` comment on the publish try/except ("reporter must not crash the host process") confirms this is exactly the guarantee the code fails to deliver. | Move the try/except in `_publish_once()` to wrap the entire body (construction + serialization + publish), not just the publish call. Reconsider whether `watchdog()` should let a best-effort sidecar task trigger a full hub shutdown at all. |
| 3 | `pyproject.toml:138` | `roxabi-obs` (and `roxabi-satellite`) excluded from pyright's `include` list — CI typecheck never analyzes it, and a real type error is hiding there | CONFIRMED | `[tool.pyright].include` omits `packages/roxabi-obs/src` and `packages/roxabi-satellite/src` despite both being full workspace members (pytest testpaths, ruff src, uv workspace all include them) — looks like an integration oversight, not documented policy. CI's bare `uv run pyright` reproduced clean (0 errors) confirming structural blindness. Running pyright directly against the excluded file reproduces a real, unannotated bug: `reporter.py:88:20 - error: Argument of type "str" cannot be assigned to parameter "health" of type "ContainerHealth" (reportArgumentType)` — `self._health = health` (line 65) widens the typed `Literal` to `str`. | Add `packages/roxabi-obs/src` (and `roxabi-satellite/src`) to `[tool.pyright].include`; fix the surfaced error with `self._health: ContainerHealth = health`. |
| 4 | `pyproject.toml:109` | `packages/roxabi-obs/tests` declared in pytest `testpaths` but no CI job ever executes it | CONFIRMED | `testpaths` only governs path-less invocations; every `uv run pytest ...` in `.github/workflows/ci.yml` (lines 185/188/191/194/220) passes an explicit path, none targeting roxabi-obs (or roxabi-blobs/roxabi-satellite, same gap). Empirically reproduced: `pytest tests/ --collect-only -q` (matching CI's exact coverage-job invocation) collects 5400 tests, zero under `roxabi_obs`. The dedicated shutdown-path regression test (`test_cancel_fleet_reporter_stops_background_task`, added this week alongside a real production fix wiring `cancel_fleet_reporter()` into 6+ entrypoint shutdown paths) has therefore never run in CI. A manual `scripts/goal-fleet-obs-evidence.sh` does invoke it but is wired to nothing CI-triggered, reinforcing rather than mitigating the gap. | Add an explicit `uv run pytest packages/roxabi-obs/tests --cov=roxabi_obs ...` step to `ci.yml` (mirroring roxabi-nats/roxabi-contracts), or invoke a path-less root `pytest`. Audit `roxabi-blobs`/`roxabi-satellite` for the same gap. |
| 5 | `apps/dashboard/src/hooks/useAgentStatus.ts:21` | Chat cockpit online/offline + harness badge shows the wrong agent's status on any multi-agent roster | CONFIRMED | `select: (rows) => rows[0]` assumes the BFF response is filtered to the requested agent; it isn't — `/api/bff/agents/status` always queries the FULL alphabetically-sorted roster, only the requested agent gets its harness override, everyone else defaults to `"claude-cli"` (`dashboard_rpc.py:227-230`). `healthFor()` checks `tabHealth.harness === harness` but never agent identity, so a coincidental harness match silently returns a different agent's health object as the active tab's. Confirmed regression via `git log -p`: prior code correctly did `status.find(h => h.agent === agent && ...)`; replaced in commit `7ce1cad4` (2026-06-28). Feeds straight into ChatPane's `offline` gate (composer/model picker) and CockpitContextPanel's online badge + harness label. Both cited tests mock single-element or alphabetically-first-coincidence responses, masking the bug. | Select the row matching the requested agent AND harness, e.g. `rows.find(r => r.agent === tabAgent)`; have the bff route return only the targeted agent's row. Add a multi-agent test where the active tab's agent is NOT alphabetically first. |
| 6 | `src/factory/bootstrap/factory/dashboard_rpc.py:229` | Bulk agents-status RPC hardcodes `harness="claude-cli"` for every non-targeted agent, ignoring each agent's real configured backend | CONFIRMED | `harness = harness_by_agent.get(name, "claude-cli")` defaults any agent not explicitly named in the request to `claude-cli`, even when its real backend is `omp-rpc` (a fully wired, first-class `HarnessKind`). The real per-agent backend IS available elsewhere in the same module set (`dashboard_agents_rpc.py:138,185` reads `row.backend`) but is never consulted here. `DashboardHome.tsx`, `OpsPage.tsx`, and `ChatSidebar` (via `useAgentStatus`) all call `fetchAgentStatus()` with no args, so this default applies to the entire bulk roster on every dashboard load. Worst case: an all-omp-rpc deployment has no CliPool/clipool-workers queue at all (only started "if any agent uses claude-cli backend"), so `clipool_alive` is permanently `False` and every agent in the roster shows `online: false` — a false fleet-wide "offline" banner despite all agents being healthy via omp-rpc. | Resolve each agent's real configured backend (via the agent store, same source `dashboard_agents_rpc.py` already uses) when computing harness/online for the bulk listing, instead of defaulting unconditionally to `claude-cli`. |

### P2 — Medium (8)

| # | File:Line | Title | Verdict | Evidence | Recommendation |
|---|---|---|---|---|---|
| 7 | `src/factory/ingress/serve.py:49` | factory-ingress restart silently re-enables a GitHub installation an operator (or GitHub itself) explicitly disconnected | CONFIRMED (adjusted high→medium: the dashboard-Disconnect half of the claim is likely unreachable today due to a separate hub/ingress `INGRESS_DB_PATH` split — different physical SQLite files — so the webhook-driven scenario is the verified live path, with reduced exploitability since GitHub stops sending events to a truly-deleted installation) | `lifespan()` runs `store.seed("github", gh_id, "default")` unconditionally on every process start when `INGRESS_GITHUB_INSTALLATION_ID` is set (the env var is documented as a permanent first-time-setup step, so it stays populated). `InstallationStore.seed()`/`upsert_lifecycle()` is an `INSERT ... ON CONFLICT DO UPDATE SET enabled = excluded.enabled` that force-sets `enabled=1` regardless of prior state. Reproduced end-to-end in one process: GitHub `installation.deleted` webhook → `apply_lifecycle()` sets `enabled=False` (`connectors/github.py:70-73`, same `InstallationStore` instance) → routine restart (converge.sh restarts every quadlet client on drift; `factory-quadlet-sync.timer` triggers full converge on any staging merge, ~every 5 min) → `lifespan()` reseeds the row to `enabled=1`. | Make the env-var seed idempotent-create-only (`INSERT OR IGNORE` / check-before-insert) instead of an unconditional upsert that overwrites `enabled`, or drop the env-var auto-seed path now that the dashboard registry flow is the canonical mutation path. |
| 8 | `src/factory/bootstrap/factory/dashboard_agents_rpc.py:85` | Dashboard soul-get RPC has no blob-absent fallback (unlike the core preload path) — raises `internal_error` instead of degrading to legacy `persona_json` | CONFIRMED (adjusted high→medium: scoped to an admin-only dashboard RPC, not the production message-handling path; outer catch-all prevents a hub crash) | `_soul_markdown_for_row()` calls `fetch_soul_markdown(blob, row.soul_document_blob_ref)` with no try/except, unlike `preload_soul_caches_for_rows()` which explicitly catches `BlobNotFoundError, BlobStoreServerError, UnicodeDecodeError, ValueError` and continues. The production message-handling path (`_resolve_system_prompt`) never hits this gap since it only reads the in-memory cache. But `handle_agents_soul_get`/`handle_agents_soul_put` (dashboard soul editor) propagate the raise through `_wrap()`'s generic catch-all into `{"error":"internal_error"}`, bypassing `_soul_sections_for_row()`'s own designed `if md: ... else: _legacy_persona_sections(row)` fallback — exactly the scenario `docs/runbooks/persona-soul-rollback.md` anticipates ("Blobstore ref points to corrupt soul.md"). No existing test covers "blob_ref set + blob store present + fetch fails". | Wrap `fetch_soul_markdown()` in `_soul_markdown_for_row` with the same exception tuple used in `preload_soul_caches_for_rows`, log a warning, fall through to `""` so the designed legacy-fallback path runs. Add a regression test for missing/corrupt blob behind a non-null ref. |
| 9 | `deploy/AGENTS.md:212` | "Network exposure tiers" table (canonical security-boundary reference for every `PublishPort`'d unit) omits `factory-ingress` and `factory-cloudflared` | UNVERIFIED | `factory-ingress.container` exposes `${TAILSCALE_IPV4}:8780:8780`, fronted by Cloudflare Tunnel for public GH/CF webhook reachability with only per-connector HMAC auth (`verify.py`) — a materially different boundary model than every other tailnet-bound row in the table, per the finder's evidence; not independently re-verified this pass. | Add `factory-ingress` and `factory-cloudflared` rows per the file's own "new exposed unit → document here" rule. |
| 10 | `packages/roxabi-obs/src/roxabi_obs/reporter.py:25` | `image_revision` is structurally dead in production — every fleet report's revision column is permanently blank | UNVERIFIED | `_read_build_revision()` only returns a value from `IMAGE_REVISION` env or `/app/.roxabi-build-info.json`; per the finder, neither is wired in any Quadlet unit or Dockerfile, only the unreachable OCI label is set, and the dashboard renders `row.image_revision ?? "—"` indistinguishable from genuinely-missing data — not independently re-verified this pass. | Inject `Environment=IMAGE_REVISION=<sha>` into Quadlet units (mirroring `IMAGE_REF=`), or write `/app/.roxabi-build-info.json` at publish time. |
| 11 | `src/factory/nats/fleet_store.py:68` | `FleetStore._rejected_ids` has no cap or eviction — grows for the hub process's entire lifetime | UNVERIFIED | `_rejected_ids: set[str]` is only ever appended to, unlike `_live` which is hard-capped (`MAX_ENTRIES=32`); per the finder the "invalid container_name" growth branch is dead code (pydantic validator already rejects those upstream) but the "registry full" branch is live — any of 13 internal NATS identities reporting >32 distinct `(host, container_name)` pairs grows the set forever — not independently re-verified this pass. | Bound `_rejected_ids` the same way `_live` is bounded, or drop the dedup-set and rate-limit the warning log instead. |
| 12 | `src/factory/infrastructure/soul/soul_ops.py:131` | Cache-coherency race between `SoulDocumentCache` warm and `AgentStore` upsert in `put_soul_document` can transiently blank an agent's prompt during concurrent reload | UNVERIFIED | Per the finder, `warm_soul_cache()` updates the cache to the NEW `blob_ref` synchronously before `await agent_store.upsert(updated)` persists the new row across two real await points; a concurrent `_resolve_system_prompt()` read during that window reads the OLD row but a cache keyed to the NEW ref, missing and falling through to `persona_json`/`""` — not independently re-verified this pass. | Warm the cache only after `agent_store.upsert()` completes, or have `_resolve_system_prompt` retain the previous composed_prompt instead of falling through on a same-agent ref mismatch that looks like an in-flight update. |
| 13 | `src/factory/bootstrap/factory/dashboard_rpc.py:235` | `# type: ignore` papers over an unvalidated `str → HarnessKind` Literal assignment in the agents-status RPC | UNVERIFIED | Per the finder, the public FastAPI route types `harness` as a plain `str` (no edge validation), so an out-of-range value propagates into the Pydantic `AgentHealth` constructor, raising a `ValidationError` that surfaces to the browser as a 502 `hub_validation_error` instead of a clean 422 — not independently re-verified this pass; finder notes the typed TS frontend always sends valid values, so not reachable through normal UI flows (API-contract robustness gap, not a live bug). | Type the FastAPI query param as `Literal["claude-cli","omp-rpc"] | None`; drop the `type: ignore` once validated/narrowed. |
| 14 | `tools/qg.conf:4` | File-length/folder-size quality gates only scan `src/*.py` — `apps/dashboard/src` (this week's largest churned subsystem) has zero enforcement | UNVERIFIED | Per the finder, `QG_FILE_ROOT`/`QG_FOLDER_ROOT`/`QG_FILE_EXTS` are Python-only, so TS/TSX files are entirely unmeasured; cites `AgentsListPanel.tsx` (414 lines) and `DesignSystemPage.tsx` (404 lines) both above the Python repo's 300-line cap, and `lib/`(32 files)/`components/ui/`(20 files) both above the 15-file folder cap, none flagged — not independently re-verified this pass. | Extend `QG_FILE_EXTS`/`QG_FILE_ROOT` (or add a second gate invocation) to cover `apps/*/src` with TS/TSX-appropriate caps, or explicitly document frontend size as out of scope. |

### P3 — Low (3)

| # | File:Line | Title | Verdict | Evidence | Recommendation |
|---|---|---|---|---|---|
| 15 | `src/factory/bootstrap/standalone/worker_standalone.py:137` | Turn-writer standalone bootstrap can leak the fleet-reporter task and NATS connection on init failure | UNVERIFIED | Per the finder, `ensure_stream`/`ensure_consumer`/`store.connect()`/`writer.start()`/`health_server.start()` run outside any try/finally, while the matching `cancel_fleet_reporter` + `nc.close()` cleanup only fires from the `finally` attached to the later `stop.wait()` — unlike every sibling standalone bootstrap (clipool/omp/adapter), which wraps the whole startup body — not independently re-verified this pass. | Wrap the init sequence in the same try/finally that already guards steady-state `stop.wait()`. |
| 16 | `deploy/nats/acl-matrix.json:1` | `container_report` ACL grants include several identities with no corresponding fleet-reporter call site or quadlet component in this repo | UNVERIFIED | Per the finder, `voice-tts`/`voice-stt`-like identities hold publish rights with no `[component.*]` section in `deploy/quadlet.toml` and no `FleetReporter` call site in `src/factory/**` — likely belong to the sibling voiceCLI repo sharing the NATS server; cannot be fully verified from this repo alone — not independently re-verified this pass. | If genuinely for voiceCLI, no action needed here; otherwise cross-repo confirm and prune over-provisioned grants. |
| 17 | `apps/dashboard/src/pages/OpsPage.tsx:19` | OpsPage log-preset dropdown labels are hardcoded French, bypassing the new react-i18next EN/FR locale system | UNVERIFIED | Per the finder, `LOG_PRESET_OPTIONS` is a module-level constant with hardcoded French strings never passed through `t()`; en/fr `ops.json` have no key for them — missed when i18n was introduced this week (commit `18e50e1e`) — not independently re-verified this pass. | Move the three preset labels into `ops.json` (en+fr), render via `t("logs.presets.*")`. |

### Duplication / cross-cutting notes (for reduce step)

- **roxabi-obs CI gate-blindness triple** (#3 `pyproject.toml:138` pyright include, #4 `pyproject.toml:109` pytest testpaths, #14 `tools/qg.conf:4` file-length/folder-size scope): not literal duplicates (three different gates, three different config keys) but the same root cause repeated — newly-added/expanded source trees (`packages/roxabi-obs`, `packages/roxabi-satellite`, and the whole `apps/dashboard/src` frontend tree) were never added to the directory-scope allowlists that make CI gates actually look at them. All three are CONFIRMED-or-credible "gate believed green tells you nothing" findings. Recommend the reduce step consolidate into a single cross-domain theme: "quality-gate directory-scope coverage has not kept pace with new packages/frontend growth" — likely recurs in other domains' synthesis too. Echoes existing memory pattern `project-quadlet-manifest-install-gap` (component declared but never wired into the enforcement path) and `feedback-doc-drift-gate-coverage-limits`.
- **Mock/test fabricates an API surface the real implementation lacks, hiding drift from CI** — the meta-failure mode behind #1 (`omp_pool.py`, tests assign `client.set_system_prompt = MagicMock()` on a bare, unspec'd mock; the real vendored `omp_rpc.RpcClient` has no such method and the package isn't installed where pyright/CI can see it) and #3 (pyright blind spot, type error ships silently). Matches existing memory items `feedback-verify-container-boot-on-m1` and `project-backend-reachable-and-protocol-conformance`: CI-green means the test passed, not that the mocked surface matches reality. Recommend a repo-wide sweep for tests that monkeypatch/fabricate attributes on bare `MagicMock()` instances for external/vendored clients, especially `omp_rpc`.
- **Just-shipped persona-soul-blobstore epic (PR #2068, merged immediately before this audit) is the common ancestor of #1, #8, #12, and (compounding) #5/#6**: the critical OMP silent-no-op (#1), the dashboard soul-editor missing-fallback (#8), and the soul-cache/agent-store race (#12) are three independent bugs in the same week-old feature surface. Recommend the reduce step flag persona-soul-blobstore as a concentrated-risk area warranting a dedicated follow-up hardening pass rather than three scattered point-fixes.
- **Dashboard agent-status harness defaulting compounds across two layers** (#5 frontend `useAgentStatus.ts` rows[0]-picking bug, #6 backend `dashboard_rpc.py` harness="claude-cli" default): different root causes at different layers (frontend array-indexing vs. backend default-value), but both independently real and both feed the same chat-cockpit online/harness badge — fixing one without the other still leaves a wrong-status display for omp-rpc-backed agents. Recommend fixing together in one PR.
- **Operator memory (`project-ingress-webhook-reachability-gap`) already tracks a related-but-distinct ingress gap** (missing cloudflared tunnel / no GitHub App) for the same `factory-ingress` subsystem; #7 and #9 in this domain found additional, different bugs (reseed-on-restart race; missing security-boundary doc row) in the same area this week — worth a single consolidated ingress-hardening follow-up rather than three independent fixes landing separately.

### Severity counts

| Severity | Count |
|---|---|
| Critical (P0) | 1 |
| High (P1) | 5 |
| Medium (P2) | 8 |
| Low (P3) | 3 |
| **Total** | **17** |

### Debt subscore: 35/100

Methodology: `100 − Σ severity_weight(sev) × verdict_confidence(verdict)`, severity_weight = {critical:15, high:7, medium:3, low:1}, verdict_confidence = {CONFIRMED:1.0, PLAUSIBLE:0.65, UNVERIFIED:0.35}. Critical(1, CONFIRMED)=15.0 + High(5, CONFIRMED)=35.0 + Medium(2 CONFIRMED + 6 UNVERIFIED)=12.3 + Low(3 UNVERIFIED)=1.05 → penalty≈63.4 → subscore≈37, rounded to 35 to reflect the concentration risk noted above (3 independent confirmed bugs in one week-old feature surface, plus a CI gate triple-blind-spot, both judged to compound risk beyond the raw weighted sum).

Rationale: 7 of 17 findings are CONFIRMED, including one critical, fully-reproduced silent feature failure (OMP persona/system-prompt never applied to any agent, shipped this week, undetectable by the existing test suite) and five CONFIRMED highs spanning a hub-crash-loop regression (today's commit), a CI typecheck+test double-blind-spot for an entire package, and a two-layer dashboard status-badge bug. The remaining 10 findings are UNVERIFIED (synthesis-stage trust, not independently re-confirmed this pass) and skew medium/low, tempering the score but not erasing the structural pattern: this week's three flagship feature areas (ingress, fleet-obs, persona-soul) each shipped with at least one confirmed silent-failure or blind-spot defect.


---

## Domain: contracts — roxabi-contracts package & cross-service wire discipline

Audit: 2026-06-30-full-audit · finder: contracts-wire · 2 findings (1 REFUTED already removed upstream — a prior `contracts-bump.yml` auto-merge-gate finding did not survive verification and is excluded from this pass)

Spot-check performed during synthesis (read source directly, not re-trusted from finder evidence alone):
- `packages/roxabi-contracts/pyproject.toml:3` → `version = "0.13.0"` confirmed.
- `packages/roxabi-contracts/CHANGELOG.md` → newest heading is still `## [0.11.0] (2026-06-11)`; grep for `0.12.0`/`0.13.0` returns zero hits anywhere in the file — confirmed.
- `git log --since="7 days ago" -- packages/roxabi-contracts/pyproject.toml` shows two bump commits this week landing after the 0.11.0 CHANGELOG entry (`0cb91264` voice-lifecycle, `aa2e79e7` fleet domain) — consistent with the finder's claim of two undocumented bumps.
- `packages/roxabi-contracts/src/roxabi_contracts/state/bot_roster.py` read in full — `RosterBotEntry` (line 28), `TelegramRosterBot` (line 41), `DiscordRosterBot` (line 52) all set `model_config = ConfigDict(extra="forbid", frozen=True)`; `public_bot: str | None = None` confirmed present on all three (lines 32/45/56). Module docstring already states the rationale: "auth fields (`owner_users`, grants) must never appear on the wire. `extra='forbid'` on entry models enforces this at parse time" — this is a pre-existing, intentional security boundary, not an oversight. Cross-repo grep confirms these models are consumed ONLY by `src/factory/infrastructure/kv/bot_roster.py` and `src/factory/bootstrap/wiring/kv_bot_roster.py` (intra-monorepo, co-deployed) — no external satellite currently imports them, corroborating the finder's own "currently low-risk" framing.

### P0 — Critical

None.

### P1 — High

None.

### P2 — Medium

None.

### P3 — Low

| ID | File:Line | Verdict | Title | Evidence | Recommendation |
|---|---|---|---|---|---|
| contracts-wire-F1 | `packages/roxabi-contracts/CHANGELOG.md:3` | UNVERIFIED (synthesis spot-check: facts confirmed) | CHANGELOG.md undocumented across two minor version bumps this week (0.11.0→0.13.0) | `pyproject.toml` at 0.13.0; CHANGELOG newest heading still `## [0.11.0]`. Two bumps (`0cb91264` voice-lifecycle, `aa2e79e7` fleet observability) landed with no CHANGELOG entry, breaking the package's own established discipline — every prior 0.4.0–0.11.0 bump documented additive-vs-breaking classification and any transitional shim with its flip-issue number (e.g. the `job_id` #1841 shim noted under 0.9.0). New `LyraEvent.tenant` additive field, the `fleet/` domain, and the voice-lifecycle domain are all unrecorded. | Backfill CHANGELOG.md entries for 0.12.0/0.13.0 documenting the additive nature of `tenant`/`sample_id`/`image_digest_status`/`public_bot` and the new fleet+voice-lifecycle domains; consider gating contracts version bumps on a CHANGELOG diff in CI so reviewers (and satellite maintainers who only see `contracts-bump.yml`'s auto-generated PR body) retain a human-readable trail. |
| contracts-wire-F3 | `packages/roxabi-contracts/src/roxabi_contracts/state/bot_roster.py:28` | UNVERIFIED (synthesis spot-check: facts confirmed; risk scope narrower than framed) | `RosterBotEntry`/`TelegramRosterBot`/`DiscordRosterBot` use `extra="forbid"` (opposite of `ContractEnvelope`'s forward-compat default `extra="ignore"`), and all three gained a field (`public_bot`) this week | `bot_roster.py:28/41/52` set `extra="forbid", frozen=True` — opposite of `ContractEnvelope`'s documented forward-compat invariant (`envelope.py:45-55`, restated in `packages/roxabi-contracts/AGENTS.md`). Commit `04c0ab2b` (this week) added `public_bot: str \| None = None` to all three models (lines 32/45/56). A consumer pinned to a pre-`04c0ab2b` version parsing a `roster.<platform>` KV doc written by a newer hub would raise a pydantic `ValidationError` on the unrecognized key — these models are silently NOT eligible for the package's additive-only minor-bump guarantee, even though nothing outside the module's own docstring signals that exception. **Mitigating context found during synthesis**: these are plain `BaseModel` subclasses, not `ContractEnvelope` subclasses, so the package-level forward-compat rule doesn't formally bind them; the docstring already documents the `extra="forbid"` choice as an intentional security boundary (auth fields must never leak onto the wire); and grep confirms zero external-satellite consumers today — risk is hypothetical, not live. | Either switch to `extra="ignore"` now that auth-sensitive fields (`owner_users`, grants) are kept out of these models by construction per the existing docstring rationale, or explicitly document in `roxabi-contracts/AGENTS.md` that `state/bot_roster.py` is intentionally exempt from the additive-only minor-bump guarantee, so future contributors don't assume bot_roster fields are wire-safe to add without coordinating consumers. |

### Duplications

None — the two surviving findings target distinct files/root causes (CHANGELOG discipline vs. model-config policy) with no overlapping evidence; no intra-domain merge needed. Not the ssot/axial domain, so no cross-domain duplication table is required here. Flagging for the reduce step: F1's "CHANGELOG is the only human-readable trail `contracts-bump.yml` doesn't auto-generate" pairs with any ci/cd-domain findings about `contracts-bump.yml`'s auto-merge path having thin gating — both point at the same workflow's lack of human-context generation, worth a single cross-domain narrative if another finder also surfaced it.


---

## Domain: error-async — roxabi-factory full audit (2026-06-30)

Finder: `err-async` · 5 findings received, 0 REFUTED (already filtered upstream), 0 merged within this domain
(no two findings share a root cause — see Notes for a same-file-region relatedness note between P1-1 and P2-1,
and a cross-domain hotspot flag for the reduce step).

Debt subscore: **58/100**

### P0 — Critical

None.

### P1 — High

| ID | File:line | Verdict | Evidence | Recommendation |
|---|---|---|---|---|
| err-async-001 | `src/factory/dashboard/routes/bff_common.py:32` (root cause); fans out to `hub_client.py:98-107` + 18 `except Exception` call sites across `bff.py` (8), `bff_admin.py` (3), `bff_agents.py` (7) | **CONFIRMED** | `map_hub_errors()` only special-cases `isinstance(exc, RuntimeError)` for the hub-unavailable→503 path. `DashboardHubClient._request()` calls `await nc.request(...)` directly on the raw `nats.aio.client.Client` (no wrapping). `nats.errors.TimeoutError`/`NoRespondersError` inherit from `nats.errors.Error(Exception)`, **not** `RuntimeError` (verified in `.venv/.../nats/errors.py` and `nats/aio/client.py:1038-1059`, which raises both unwrapped from `.request()`). Every BFF route's `except Exception as exc: mapped = map_hub_errors(exc); ...; raise` therefore lets a hub-restart/overload/NATS-responder-gap condition fall through unmapped. No `exception_handler` exists on `create_dashboard_app()` (grepped entire dashboard/web-adapter path, zero hits). Reproduced empirically: raising `NoRespondersError`/`TimeoutError` through the real `map_hub_errors` + FastAPI `TestClient` yields a raw `500`, not the intended `503` from `hub_unavailable()`. `tests/adapters/web/test_dashboard_bff.py` has zero references to either exception class. Corroborating pattern: `src/factory/transport/nats_request_response.py`'s `NatsTransport.call()` already catches exactly these classes on raw `nc.request()` elsewhere in the codebase — `DashboardHubClient` bypasses that established pattern entirely. | Add `nats.errors.Error` (or the two specific subclasses) to `map_hub_errors()`'s RuntimeError branch so hub-RPC-unreachable conditions map to the existing `hub_unavailable()` 503 helper. Add a regression test asserting a simulated `nc.request` timeout returns 503, not 500. |
| err-async-002 | `src/factory/nats/fleet_digest.py:54` (root); chain through `fleet_store.py:118-125` → `fleet_rpc.py:38` | **CONFIRMED** | `load_fleet_digest_state()` does a synchronous `target.read_text(...)` at line 54. `FleetStore.list_snapshot()` is a plain `def` (not `async def`) that calls it directly at line 125. `handle_fleet_list()` — an `async def` NATS RPC handler registered as the hub's `SUBJECTS.fleet_list` responder — awaits `list_snapshot()` unwrapped, inside an `nc.subscribe()` callback running on the hub's single shared asyncio event loop (same loop driving all inbound routing / LLM streaming / outbound delivery). No `asyncio.to_thread`/`run_in_executor`/`ThreadPoolExecutor` exists anywhere in this path (independently grepped). Dashboard `FleetPage.tsx` polls this RPC every 30s per open operator tab; a separate host process (`tools/fleet_digest_poll.py`) does a non-atomic `write_text()` to the same file, adding concurrent-writer risk. Violates the project rule "All I/O is async — never block the event loop" (`src/factory/adapters/AGENTS.md`, verified verbatim). | Wrap the read in `await asyncio.to_thread(load_fleet_digest_state)` inside `handle_fleet_list()`, or make `load_fleet_digest_state`/`list_snapshot` async via `aiofiles`, so the hub event loop is never blocked by this disk read. |

### P2 — Medium

| ID | File:line | Verdict | Evidence | Recommendation |
|---|---|---|---|---|
| err-async-003 | `src/factory/dashboard/routes/bff.py:75` (+17 sibling sites: `bff.py:95,107,130,144,164,202,225`, `bff_admin.py:29,39,55`, `bff_agents.py:29,39,49,59,69,79,91`); plus `user_store_profile.py:65,124` and `clipool_worker.py:170` | UNVERIFIED | AGENTS.md:33 requires `except Exception:` to carry an inline justification comment, with a follow-up issue if swallowing. ~40 other `except Exception` sites repo-wide consistently carry a `# noqa: BLE001 — DEBT:boundary-broad-catch# boundary: ...` tag. Independently re-confirmed the exact 18 bare `except Exception as exc:` line numbers in `bff.py`/`bff_admin.py`/`bff_agents.py` (PR #2070, this week's dashboard-admin-users-agents split) — all 18 match exactly, zero carry the tag. `clipool_worker.py:170` allegedly has `# noqa: BLE001` with no `DEBT:boundary` tag, unlike siblings in the same subtree (not independently re-checked at byte level by this synthesis pass). | Add the established `# noqa: BLE001 — DEBT:boundary-broad-catch# boundary: dashboard-bff — hub RPC error translation` (or equivalent) comment to each site so the AGENTS.md governance rule is grep-satisfiable and the axial-review checklist item isn't silently drifting on the week's largest feature. |

### P3 — Low

| ID | File:line | Verdict | Evidence | Recommendation |
|---|---|---|---|---|
| err-async-004 | `src/factory/adapters/omp/_rpc_bridge_callbacks.py:106` | UNVERIFIED | `_schedule_publish._spawn()` does `task = asyncio.create_task(nc.publish(subject, payload)); task.add_done_callback(_log_publish_result)` with `task` only a local variable — independently confirmed at lines 104-107. Per asyncio docs, the event loop holds only a weak reference; an unreferenced task may be GC'd before completion. Every other `create_task` site in the codebase (`clipool_worker.py` `_jobs`, `outbound_dispatcher.py` `_worker`, `jetstream_audio_consumer.py` `_task`) stores the task on an instance attribute/tracked set specifically to avoid this — this is the one call site in `src/factory` that doesn't. Theoretical risk (loop-thread `call_soon_threadsafe` dispatch makes premature collection unlikely in practice), hence low severity. | Store the task on a module/instance-level set (e.g. `self._pending_publishes`), discarding on done, matching the codebase's established convention. |
| err-async-005 | `packages/roxabi-nats/src/roxabi_nats/readiness.py:59` | UNVERIFIED | `_open_or_create_lyra_state_kv()` catches any `BadRequestError` from `js.create_key_value(...)` and unconditionally falls back to `js.key_value('factory-state')` — independently confirmed at lines 39-60. The sibling `open_or_create_kv()` (`src/factory/infrastructure/kv/factory_state.py:10-28`) correctly checks `exc.err_code != 10058` ("stream name already in use") before re-raising — independently confirmed, code reads exactly as described. `packages/roxabi-nats` cannot import `factory.*` (package boundary rule), so the two copies have silently drifted: a non-race `BadRequestError` (e.g. misconfigured `KeyValueConfig`) in `announce_hub_ready`'s path is silently swallowed and mis-treated as "lost the race," masking the real config error behind a second `js.key_value()` call. | Port the `err_code == 10058` check into `roxabi_nats.readiness._open_or_create_lyra_state_kv` (self-contained against `nats.js.errors.BadRequestError`, no `factory.*` import needed) so both copies fail closed on genuine config errors. |

### Notes

- **Same-region, distinct-defect relatedness (err-async-001 ↔ err-async-003):** both findings cite the *exact same* 18 `except Exception` call sites across `bff.py`/`bff_admin.py`/`bff_agents.py` (PR #2070, this week). err-async-001 is a correctness bug (wrong exception taxonomy → 500 instead of 503 on hub-RPC-unreachable); err-async-003 is a governance/hygiene gap (missing AGENTS.md-mandated inline comment). They are not duplicates — different root cause, different fix shape — but a single PR touching these 18 sites should fix both: narrow/extend the `map_hub_errors` mapping AND add the justification comment in the same pass.
- **Cross-domain hotspot flag for reduce step:** `bff_admin.py` and `bff_agents.py` (this week's PR #2070 dashboard-admin-users-agents split) are independently implicated by error-async (this shard, both as P1-correctness and P2-hygiene) and reportedly by the security/ssot domain shards for an unauthenticated-access finding on the same files (per the audit's prior cross-domain summary). The reduce step should verify whether the security-domain finding on `bff_admin.py`/`bff_agents.py` is the *same* code region as err-async-001/003 (likely adjacent but distinct: authn-missing vs. error-mapping vs. comment-hygiene) before merging — do not collapse into one finding without confirming the file:line ranges are the same defect.
- All 5 findings trace to 2 fresh feature branches: the dashboard-admin-users-agents BFF split (PR #2070, err-async-001/003) and unrelated pre-existing code (fleet_digest, omp rpc bridge, roxabi-nats readiness — err-async-002/004/005). No single root cause unifies all 5; this is a dispersed set of independent async/error-handling gaps, not one systemic issue.
- Both P1 findings were reproduced/traced end-to-end by this synthesis pass (class hierarchy read directly from `nats/errors.py` + `nats/aio/client.py` for 001; full call chain read for 002) and stand as CONFIRMED with high confidence. The 3 UNVERIFIED findings (003/004/005) were independently spot-checked at the cited file:line for 003 (exact line-number match across all 18 sites) and 004/005 (full code read, both match the evidence verbatim) — no factual errors found in any of the three, but verdicts are left as submitted (UNVERIFIED) per the synthesis contract since they were not exercised end-to-end (no reproduction/test).


---

# Appendix A — Coverage note (run-1 gap → run-2 fix)

The audit is complete, but only after a second pass. Recorded here so the gap is auditable and the lesson is reusable.

**Run-1 (69 agents, task `w8dm0bh6z`)** — 6 finders returned no structured output (`subagent completed without calling StructuredOutput`), exactly the ones using custom agentTypes:

| Finder | agentType (run-1) | Outcome |
|---|---|---|
| `axial-repo`, `axial-week` | `dev-core:axial-adr-review` | **0/2** returned — `axial-drift` domain entirely empty |
| `sec-ingress`, `sec-authz`, `sec-secrets-acl`, `sec-blobstore-soul` | `dev-core:security-auditor` | **0/4** returned — `security` domain had only `sec-dashboard`'s 2 findings |

Root cause: those agents' system prompts hard-bias to Conventional-Comments prose and don't reliably call the injected structured-output tool → `agent({schema})` returns `null` → the domain silently shows near-zero findings. Every `Explore`-based finder succeeded.

**Run-2 (resume, task `w8d04yukd`)** — switched only those 6 finders to `agentType: 'Explore'` (proven reliable with schema); the 20 successful finders + their verifiers returned from cache; synthesis regenerated. Result: `axial-drift` 0→4, `security` 2→19, total deduped 68→77.

**Reusable lesson (saved to memory):** for `Workflow` `agent()` calls that pass a `schema`, use `agentType: 'Explore'` (or omit) and put the specialist lens in the prompt; reserve custom write-agentTypes for non-schema phases. On resume, don't edit shared prompt builders — it busts every finder's cache.

# Appendix B — Run metadata

| | Run-1 | Run-2 (resume) |
|---|---|---|
| Task ID | `w8dm0bh6z` | `w8d04yukd` |
| Run ID | `wf_d48f816c-d4e` | same (resumed) |
| Agents | 69 | 83 (incl. cached replays) |
| Subagent tokens | 5,989,890 | 6,758,610 |
| Tool uses | 2,534 | 2,652 |
| Duration | ~29.6 min | ~28.2 min |
| Domains returned | 7 (no axial-drift) | 8 (complete) |
| Deduped findings | 68 | **77** |
| Debt score | 32/100 | **38/100** |

**Finder matrix (26 finders, 8 domains).** All read-only. `scope`: repo / week (last-week delta) / cross-repo (`~/projects`).

| Domain | Finders |
|---|---|
| architecture | `arch-core`, `arch-adapters`, `arch-bootstrap`, `arch-infra` |
| axial-drift | `axial-repo`, `axial-week` |
| security | `sec-ingress`, `sec-authz`, `sec-secrets-acl`, `sec-blobstore-soul`, `sec-dashboard` |
| ssot | `ssot-config`, `ssot-stores`, `ssot-nats`, `ssot-docs`, `ssot-ccc-sweep`, `ssot-crossrepo` |
| deploy | `deploy-factory`, `deploy-cluster` (cross-repo), `deploy-acl-pipeline` |
| week-subsystem | `week-ingress`, `week-fleet-obs`, `week-soul`, `week-dashboard-ux` |
| contracts | `contracts-wire` |
| error-async | `err-async` |

Pipeline shape: `ground-truth (1)` → `pipeline(finder → adversarial-verify of crit/high)` → `map (per-domain synth, ≤8) → reduce (1 meta-synth)`. Verify + synth were read-only except the synth agents, which wrote only under the audit dir (no git).

# Appendix C — Reproduce / next actions

**Re-run:**
```
Workflow({ scriptPath: "~/projects/roxabi-factory/artifacts/plans/full-audit.wf.js" })
```
Tunables at the top of the script: `WEEK_SINCE`, `VERIFY_SEVERITIES`, the `FINDERS[]` matrix.

**Proposed next actions (awaiting operator go — nothing filed/deployed):**
1. **Check M₁ live** — `ssh roxabituwer` (read-only): is the vulnerable dashboard image actually deployed/running?
2. **Patch dashboard cluster** — P0-1 + P0-2 + P1 rebind, one worktree → PR `--base staging` (wire `require_operator` on `bff.py`/`bff_admin.py`/`bff_agents.py` + ownership check on `jobs/steer` + require the operator token secret).
3. **File the 5 P0 + 13 P1** via `roxabi-issues:issue-triage` (native labels/relations; check epic #2034 first to avoid overlap on P0-3/P0-5 deploy items).
4. **Confirm the caveated P0s** — P0-3 (roxabi-nats kv.watch fallback), P0-4 (`omp_rpc` at M₁ runtime), P0-5 (`cluster_plan.py` --prune path).

*Consolidated by the Claude lead on 2026-07-01. Parts I and II are the verbatim agent output (headings demoted one level); Part 0 and the appendices are lead-authored.*
