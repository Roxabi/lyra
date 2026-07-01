# roxabi-factory — Full Audit Summary (2026-06-30)

> **Remediation addendum:** 2026-07-01 — session audit 2026-06-30, 4 PRs mergées (#2077, #2084, #2085, #2086). Findings snapshot ci-dessous inchangé ; statut courant dans [Remediation status](#remediation-status-session-2026-06-30--merges-2026-07-01).

Reduce pass over **8 domain shards** (architecture, axial-drift, security, ssot, deploy, week-subsystem, contracts, error-async). Week-over-week window: 392 commits since 2026-06-23 (base `2714b361` → `staging`), dominated by the operator-console redesign (PR #2070, `feat/dashboard-admin-users-agents`) and fleet container observability post-MVP work (`feat/fleet-container-obs-post-mvp`), merged together in `00406cdd`, plus the ingress-connector-tenant-contract thread (ADR-096).

**All 16 quality gates pass clean** (ccc-index, importlinter 15/15, doc_drift, doc_semantic_drift, secrets_drift, secrets_source, quadlet_manifest_install, quadlet_component_source, str_exc_bus_bound, hardcoded_constants, file_length, folder_size, test_sleep, omp_pin_lockstep, architecture_snapshot, volumes_table). Green-CI gives **zero protection** against 4 of this audit's 5 P0 findings — the dashboard-BFF auth bypass, the jobs/steer IDOR, the unbackported NATS ACL grant, and the OMP `set_system_prompt` API mismatch — none of these are gate-checkable today.

> **Data provenance note**: `by-domain/ssot.md` and `by-domain/deploy.md` each report that a *different, larger* prior finding set previously existed at their file paths. Three of those items are now **RESOLVED** (see remediation table): `quadlet_component_source` gate wired (#2077), `ingress.toml` auto-provisioned (#2085), `converge.sh` `NO_RESTART=1` daemon-reload skip (#2086). Still **open / not in reduce JSON**: `cluster_plan.py:75-82` managed_repos allowlist silent-drop. Domain shards (`by-domain/*.md`) remain verbatim snapshots — not re-run.

## Remediation status (session 2026-06-30 → merges 2026-07-01)

| PR | Contenu | Findings / items clos | État |
|---|---|---|---|
| [#2077](https://github.com/Roxabi/roxabi-factory/pull/2077) | quick-wins : pyright `roxabi-obs`/`roxabi-satellite`, CI coverage `roxabi-obs/tests`, `deployment.md` topology pointer, `security-routing.md` ADR-090, `reporter.py` type fix | Quick wins **#1–4** ; P1-4 (doc topology), P1-5 (ADR-090 doc), P1-8 (pyright), P1-9 (CI test job) ; bonus gate **#2038** `check_quadlet_component_source` | ✅ mergée |
| [#2084](https://github.com/Roxabi/roxabi-factory/pull/2084) | gate `stale_container_count` : message sans compte hardcodé + régression | ssot doc-drift tooling (pas un P0–P3 numéroté) | ✅ mergée |
| [#2085](https://github.com/Roxabi/roxabi-factory/pull/2085) | `ingress.toml` auto-provisionné (`install.sh` + `setup.py`) + tests | Provenance note : `ingress.toml never provisioned` | ✅ mergée |
| [#2086](https://github.com/Roxabi/roxabi-factory/pull/2086) | `converge.sh` : `daemon-reload` après `NO_RESTART=1` + test ordre reload → restart | Provenance note : `converge.sh` daemon-reload skip | ✅ mergée — **vérif M₁** recommandée au prochain auto-converge |

**Encore ouverts (plan session — 4 items) :**

| Item | Finding audit | Décision opérateur |
|---|---|---|
| **obs hardening** | P1-7 — `FleetReporter` crash guard (`reporter.py:82`) | ✅ implémenté — try/except sur construction+publish ; tests régression |
| **omp P0-5** | `omp_pool.py` `set_system_prompt` no-op | Respawn worker + `append_system_prompt` au constructeur (reco opérateur) |
| **acl P0-4** | telegram/discord manquent `$JS.API.STREAM.NAMES` (+ `CONSUMER.INFO`) | OK merge via auto-converge → restart NATS M₁ (sinon merge manuel hors converge) |
| **cluster-plan P0-3** | `cluster_plan.py` role-rename + `--prune` (cross-repo) | Préflight souple hosts.toml ↔ quadlet.toml (reco opérateur) |

**Toujours ouverts (hors plan session, non traités) :** P0-1 dashboard BFF auth, P0-2 jobs/steer IDOR, et le reste du backlog P1–P3 (77 findings snapshot → ~10 clos ou partiellement clos via #2077).

## Executive summary

The week's two flagship features (dashboard admin/agents + fleet observability) introduced the audit's most severe finding: dashboard BFF admin/agent/job-control routes ship with **zero operator authentication**, independently confirmed by both the **security** domain (5/5 finders converged, 6 raw findings merged into one) and the **ssot** domain (SSoT-vocabulary check against `AgentGrantStore`/ADR-090) — same files (`bff_admin.py`, `bff_agents.py`), same root cause, same week. This is a Tailnet-reachable, net-new production surface with real mutation power (agent-grant writes, system-prompt overwrite, cross-session text injection, identity rebind) and is the unambiguous top fix priority.

Four more P0s round out the critical tier: an independent IDOR on `/api/bff/jobs/steer` (security), an unattended `cluster_plan.py --prune` role-drift hazard that can silently delete live production Quadlet units on a `hosts.toml` role rename (deploy), a never-backported NATS ACL grant gap that already crash-looped the dashboard once this week (deploy), and a silently-broken OMP persona/soul system-prompt apply — the just-shipped `omp_pool.py` calls `set_system_prompt`, a method that does not exist on the real `omp_rpc.RpcClient`, undetected because the test suite mocks an attribute the real client never had (week-subsystem). The OMP bug is the clearest instance of this audit's recurring "registered/mocked ≠ reachable/conformant" failure mode (matches operator memory `project-backend-reachable-and-protocol-conformance.md`).

Debt is heavily concentrated in **ssot** (22 findings, subscore 40/100 — doc/topology drift, dead-but-declared SSoT subjects, duplicated KV-bucket idioms) and **security** (13 findings, subscore 15/100 — the lowest subscore of any domain, reflecting one severe, fresh, multi-confirmed access-control failure rather than diffuse debt). **contracts** (90/100) and **axial-drift** (76/100) are the healthiest domains this cycle.

## Global Technical Debt Score: **38 / 100** (100 = pristine)

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

## Severity totals

| | Critical | High | Medium | Low | Total |
|---|---:|---:|---:|---:|---:|
| Raw (sum of all 8 domain shards) | 6 | 13 | 38 | 22 | 79 |
| **Deduped (this reduce pass)** | **5** | **13** | **37** | **22** | **77** |

Dedup removed 1 critical (security's dashboard-BFF-auth finding ≡ ssot's `ssot-ccc-sweep-001`, same root cause) and 1 medium (architecture's socialmedia `str(exc)` leak symptom ≡ security's `check_str_exc_bus_bound.sh` gate-blind-spot root cause — same gate, same verified false-negative). No high/low cross-domain exact duplicates found. See merge table below.

## Cross-domain duplicate merges

| Merged finding | Domains (independent confirmation) | Files | Resolution |
|---|---|---|---|
| **P0 — Dashboard admin/agent/job-control BFF routes have zero authentication** | security (critical, merges 6 raw findings, 5/5 finders) + ssot (critical, `ssot-ccc-sweep-001`) | `src/factory/dashboard/routes/bff_admin.py`, `bff_agents.py` | One P0 issue. Two domains reached the same conclusion via different methods (security: route-by-route auth-decorator diff vs. `routes/connectors.py`; ssot: SSoT-vocabulary check against `AgentGrantStore`/ADR-090) — confidence raised to maximum. |
| **P2 — `str_exc_bus_bound` gate has blind spots that let raw exception text reach bus-bound error fields** | architecture (medium, symptom: `socialmedia/adapter.py:295` leak) + security (medium, root cause: `tools/check_str_exc_bus_bound.sh:12,27` regex/scan-root gap) | `src/factory/adapters/socialmedia/adapter.py:295`, `tools/check_str_exc_bus_bound.sh:12,27` | One P2 issue covering both the gate fix and its two known false-negative sites (socialmedia adapter, confirmed by architecture; dashboard admin RPCs, confirmed by security). |

### Checked-but-not-duplicate (overlapping files, distinct defects — kept separate)

- **Shared hotspot, not a dup**: error-async's `err-async-001`/`err-async-003` (18 `except Exception` sites missing AGENTS.md-mandated inline justification, and a wrong-exception-taxonomy 500-vs-503 bug) sit in the **same files** (`bff.py`, `bff_admin.py`, `bff_agents.py`) as the P0 auth-bypass merge above, but are different defect classes (missing-comment / wrong-error-mapping vs. missing-authorization). Fix in one coordinated pass on these files, but tracked as separate issues.
- ssot's `docs/architecture/security-routing.md:87` (ADR-090 described as future work) was checked against security's 2 findings (dashboard-BFF auth, LogQL injection) — does **not** cover ADR-090 staleness. Kept standalone (P1).
- security's `sec-authz-002` (ADR-090 admin-bypass not implemented — availability bug) and ssot's `security-routing.md:87` (ADR-090 doc says "planned" when it's shipped) both cite ADR-090 but describe different specific defects (implementation gap vs. doc staleness) — kept separate, flagged below as a thematic cluster.

## Thematic clusters (not merged — distinct root causes, same subsystem)

1. **NATS-KV id-sanitization fragility** — architecture's `kv_safe_part` non-injective collision (`_kv_keys.py:13`, **P1, CONFIRMED** — distinct `pool_id`s collide on the same sanitized KV key) sits in the same surface as ssot's duplicated create-or-open KV-bucket idiom (5+ modules, one already drifted, `ssot-ccc-sweep-003`) and architecture's duplicated NATS-identifier regex with no parity test (`roxabi_satellite/tokens.py:11`). This is the same surface that caused a real prior outage (operator memory `project-natskv-poolid-colon-key-outage.md`, colon-sanitization bug). Recommend one consolidated NATS-KV-id-hygiene workstream.
2. **`hosts.toml` role-SSoT gap** — deploy's P0 (`cluster_plan.py:45`, unattended `--prune` deletes live prod units on role rename) and deploy's own self-flagged dup (`deploy/lib/quadlet-units.sh:12`, role-blind client-restart fan-out) sit alongside ssot's `deploy/quadlet.toml:167` (`network.roxabi` `host_roles` scoped to `factory-hub` only, breaks fresh M2 provisioning). Three distinct bugs, one root deficiency: no single canonical role-validation SSoT across `hosts.toml` and every managed repo's `quadlet.toml`. Matches operator memory `project-hosts-role-rename-fanout.md` exactly. Recommend a shared `~/projects/lib/` role-validation helper, not three separate point-fixes.
3. **OMP/omp-rpc composition + protocol fragility** — architecture found `unified.py` ("factory start") registers an omp-rpc backend it can never construct in-process; week-subsystem separately found that where OMP **is** constructed (`omp_pool.py`), it calls `set_system_prompt`, a method the real `omp_rpc.RpcClient` does not have (only the test's bare `MagicMock()` has it) — this is this audit's sole P0 in week-subsystem. Two distinct bugs, same subsystem, same "registered/mocked ≠ reachable/conformant" pattern as operator memory `project-backend-reachable-and-protocol-conformance.md`. Recommend one OMP composition-root + protocol-conformance audit.
4. **ADR-090 fidelity drift** — referenced by 3 distinct findings across 2 domains: security's P0-adjacent admin-bypass-not-implemented (availability bug), ssot's doc-staleness (`security-routing.md:87` says "planned", it's shipped), and security's blast-radius note that the dashboard P0 is the same surface ADR-090 was meant to gate. Not merged (different specific defects) but should be fixed as one ADR-090 hardening pass.
5. **`str_exc_bus_bound` gate blind spot** — see merge table above (architecture + security).
6. **roxabi-obs CI onboarding gap** — week-subsystem-internal pair: `pyproject.toml:138` (pyright include omits `packages/roxabi-obs/src`) + `pyproject.toml:109` (pytest testpaths lists `roxabi-obs/tests` but no CI job ever passes that path) — same root cause (new package never added to CI directory-scope allowlists), one fix closes both P1s.
7. **Silent-failure cluster (zero error signal on brand-new code)** — week-subsystem alone: OMP persona apply silently no-ops (P0), soul cache-miss fallback can transiently blank a live system prompt (P2), ingress connector-restart silently re-seeds a deleted webhook (P2). All three are zero-error-signal failures in code shipped this week. Recommend a standing silent-failure audit practice, not a one-off fix.
8. **Dashboard admin/agent surface concentration** — the persona-soul-blobstore epic (PR #2068, merged immediately pre-audit) is the common ancestor of `omp_pool.py:64` (P0), `dashboard_agents_rpc.py:85` (soul-get missing blob-fallback, P2), and `soul_ops.py:131` (cache/store race, P2) — concentrated-risk feature surface; recommend a dedicated hardening follow-up rather than scattered point-fixes.

## Axial-drift summary table

`axial-drift` is this cycle's healthiest domain by volume (4 findings, subscore 76/100) but flags one pattern worth tracking against ADR-073 ("Axial Stage-of-Pipeline Decomposition" — 3+ sibling copies of cross-cutting logic is the actionable threshold):

| Severity | File:Line | Finding | Status |
|---|---|---|---|
| medium | `src/factory/llm/claude_job_codec.py:24` | `WorkerError` validation helper triplicated across claude/omp/cli_pool codec modules | CONFIRMED (downgraded high→medium: behaviorally identical today, prospective risk only) |
| medium | `apps/dashboard/src/components/agents/AgentsListPanel.tsx:287` | Telegram/Discord/Email presence rendering hardcoded across 5 dashboard sites instead of one platform list | UNVERIFIED |
| low | `src/factory/bootstrap/factory/dashboard/admin_rpc.py:45` | `_agent_store(hub)` helper byte-identically duplicated across two dashboard RPC modules (PR #2070's file-length split left it un-consolidated) | UNVERIFIED |
| low | `src/factory/ingress/` | New ingress subsystem (ADR-096) has no `AGENTS.md` documenting the stage-axis Connector contract | UNVERIFIED |

Note: finding #1 (codec triplication) is the clearest textbook ADR-073 hit this cycle. Findings #2+#3 are both symptoms of the same PR #2070 file-length split skipping shared-helper extraction — flagged as one systemic root cause if other domains hit `admin_rpc.py`/`dashboard_agents_rpc.py` again.

## SSoT / duplication table

Beyond the two cross-domain merges above, `ssot.md` independently surfaces its own internal duplication clusters (not cross-domain, kept within ssot's own count of 22):

| Cluster | Files | Severity | Note |
|---|---|---|---|
| KV bucket create-or-open idiom (3-way merge) | `infrastructure/kv`, `outbound_audio`, `stores/jobs`, `stores/kv`, `blobstore` (5 modules) + `roxabi-nats` | low (downgraded from high — near-zero production blast radius) | `ssot-ccc-sweep-003` ⊃ `ssot-stores-1` ⊃ `ssot-stores-3`, same try-create→`BadRequestError`/`BucketNotFoundError`→bind logic |
| Container/component enumeration drift | `deploy/install.sh:403`, `docs/architecture/deployment.md:7`, `AGENTS.md:86` | medium/high | 3 independent hand-maintained lists, none derived from `docs/architecture/CURRENT.generated.md` Process Topology |
| Dashboard operator-auth pair | `bff_admin.py`/`bff_agents.py` (P0, merged above) + `hmac.compare_digest` reimplemented 6x with no shared `verify_shared_secret` primitive | critical + medium | ssot's own evidence: the duplication pattern is what let the P0 auth gap ship unnoticed |
| Cross-repo deploy/install convention divergence | `secrets-policy.toml` (factory-only) + `install.sh` diverged 4 ways across factory/voiceCLI/llmCLI/imageCLI | medium | both stem from absence of a shared `~/projects/lib/install-common.sh` |

## Deploy section

7 findings (3 finders: deploy-factory, deploy-cluster, deploy-acl-pipeline), subscore 33/100. Two P0s:

- `lib/cluster_plan.py:45` — `hosts.toml` role renames desync silently from per-repo `quadlet.toml` `host_roles`; an unattended `--prune` run can delete live production units. **CONFIRMED.** Part of thematic cluster #2 above (`hosts.toml` role-SSoT gap).
- `deploy/nats/acl-matrix.json:94` — telegram/discord-adapter missing `$JS.API.STREAM.NAMES` + `CONSUMER.INFO` grants for `wait_for_hub()`'s `kv.watch` fallback. **CONFIRMED** — the *same bug class* already crash-looped the dashboard this same week and was fixed there but never backported to the adapters.

Three mediums round out the domain: S12 (`RestartForceExitStatus=`) carve-out missing from all 22 Quadlet units (unlike voiceCLI/llmCLI siblings); `quadlet-units.sh` bypassing `cluster_plan.py`'s host-role SSoT (self-flagged dup of the P0, kept separate — different failure mechanism); and the hand-mirrored ACL parity fixture (`v3-pre-grant-group.json`) with zero local pre-push enforcement (only full CI catches drift — matches operator memory `project-acl-identity-regen-fanout.md`). Two lows: `make quadlet-install` redundantly re-installs the static unit fleet (severity downgraded high→low on verify — confirmed idempotent, just wasteful); `factory-langfuse-web`/`-worker` `RestartSec=15` undocumented deviation from the S12 standard of 10.

## P0 — Critical (5)

| # | Finding | Domain(s) | File:Line |
|---|---|---|---|
| P0-1 | Dashboard admin/agent/job-control BFF routes have zero operator authentication — self-service agent-grant escalation, persona/system-prompt hijack, identity disclosure | security + ssot (merged) | `src/factory/dashboard/routes/bff_admin.py:24-64`, `bff_agents.py:22-95` |
| P0-2 | `/api/bff/jobs/steer` has no auth and no job-ownership check — IDOR text-injection into any live agent session | security | `src/factory/bootstrap/factory/dashboard_jobs_rpc.py:100` |
| P0-3 | `hosts.toml` role renames desync silently from per-repo `quadlet.toml` `host_roles`; unattended `--prune` deletes live production units | deploy | `lib/cluster_plan.py:45` |
| P0-4 | telegram/discord-adapter missing `$JS.API.STREAM.NAMES` + `CONSUMER.INFO` grants for `wait_for_hub()` `kv.watch` fallback — same bug crash-looped dashboard same day, never backported | deploy | `deploy/nats/acl-matrix.json:94` |
| P0-5 | OMP V2 calls nonexistent `set_system_prompt` on the real `omp_rpc.RpcClient` — persona/soul silently never applied to any OMP-backed agent | week-subsystem | `src/factory/adapters/omp/omp_pool.py:64` |

Full detail: [`by-domain/security.md`](by-domain/security.md), [`by-domain/ssot.md`](by-domain/ssot.md), [`by-domain/deploy.md`](by-domain/deploy.md), [`by-domain/week-subsystem.md`](by-domain/week-subsystem.md)

## P1 — High (13)

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

## P2 — Medium (37, condensed)

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

## P3 — Low (22, condensed)

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

## Metrics dashboard — per-domain issues × severity

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

## Triage table — proposed GitHub issues

Proposals only at audit time — remediation PRs listed in [Remediation status](#remediation-status-session-2026-06-30--merges-2026-07-01). New mutations go through `roxabi-issues:issue-triage` per `~/projects/ssot/operator.ssot.md`.

| Priority | Suggested issue title | Owning domain | File:Line | Status (2026-07-01) |
|---|---|---|---|---|
| P0 | `fix(dashboard): require operator auth on admin/agent BFF routes (bff_admin.py, bff_agents.py)` | security (+ssot) | `src/factory/dashboard/routes/bff_admin.py:24` | **open** |
| P0 | `fix(dashboard): authenticate + check job ownership on /api/bff/jobs/steer (IDOR)` | security | `src/factory/bootstrap/factory/dashboard_jobs_rpc.py:100` | **open** |
| P0 | `fix(deploy): make cluster_plan.py --prune role-validation hosts.toml-aware before deleting live units` | deploy | `lib/cluster_plan.py:45` | **open** — plan session, décision préflight |
| P0 | `fix(nats): backport $JS.API.STREAM.NAMES + CONSUMER.INFO grants to telegram/discord-adapter ACL` | deploy | `deploy/nats/acl-matrix.json:94` | **open** — plan session, décision restart NATS |
| P0 | `fix(omp): omp_pool.py calls nonexistent RpcClient.set_system_prompt — persona/soul never applied` | week-subsystem | `src/factory/adapters/omp/omp_pool.py:64` | **open** — plan session, décision respawn |
| P1 | `fix(nats-kv): kv_safe_part collision — distinct pool_ids map to the same sanitized KV key` | architecture | `src/factory/infrastructure/stores/kv/_kv_keys.py:13` | open |
| P1 | `fix(dashboard): admin_rpc.py PATCH allows identity rebind onto arbitrary user_id` | security | `src/factory/bootstrap/factory/dashboard/admin_rpc.py:241` | open |
| P1 | `fix(dashboard): enforce soul secret-lint server-side, not just cosmetic client warning` | security | `apps/dashboard/src/lib/soul-secret-lint.ts:1` | open |
| P1 | `docs(architecture): fix deployment.md container topology (9 → 20 containers)` | ssot | `docs/architecture/deployment.md:7` | **closed** [#2077](https://github.com/Roxabi/roxabi-factory/pull/2077) — pointer `CURRENT.generated.md` + 16 active / 23 declared |
| P1 | `docs(architecture): fix security-routing.md ADR-090 status (planned → shipped)` | ssot | `docs/architecture/security-routing.md:87` | **closed** [#2077](https://github.com/Roxabi/roxabi-factory/pull/2077) |
| P1 | `fix(deploy): scope network.roxabi host_roles to include M2 (llm-worker/image-worker)` | ssot | `deploy/quadlet.toml:167` | open |
| P1 | `fix(obs): move FleetReporter report-construction inside the crash guard` | week-subsystem | `packages/roxabi-obs/src/roxabi_obs/reporter.py:82` | **closed** — obs hardening (construction+publish guard) |
| P1 | `fix(ci): add packages/roxabi-obs(+roxabi-satellite) to pyright include` | week-subsystem | `pyproject.toml:138` | **closed** [#2077](https://github.com/Roxabi/roxabi-factory/pull/2077) |
| P1 | `fix(ci): wire packages/roxabi-obs/tests into an actual CI test job` | week-subsystem | `pyproject.toml:109` | **closed** [#2077](https://github.com/Roxabi/roxabi-factory/pull/2077) |
| P1 | `fix(dashboard): useAgentStatus.ts picks rows[0] instead of the selected agent` | week-subsystem | `apps/dashboard/src/hooks/useAgentStatus.ts:21` |
| P1 | `fix(dashboard): bulk agent-status RPC stops defaulting non-targeted agents to harness=claude-cli` | week-subsystem | `src/factory/bootstrap/factory/dashboard_rpc.py:229` |
| P1 | `fix(dashboard): BFF error-mapping catches NATS RPC timeout/no-responders as 503` | error-async | `src/factory/dashboard/routes/bff_common.py:32` |
| P1 | `fix(hub): fleet_digest.py — move blocking sync file I/O off the event loop` | error-async | `src/factory/nats/fleet_digest.py:54` |

## Top-10 quick wins (high impact / low effort)

1. ~~**Add `packages/roxabi-obs` to pyright include**~~ — ✅ **#2077**
2. ~~**Wire `packages/roxabi-obs/tests` into a real CI job**~~ — ✅ **#2077** (`Coverage — roxabi_obs`, floor 69%)
3. ~~**Fix `deployment.md` container count**~~ — ✅ **#2077** (pointer `CURRENT.generated.md`, plus de compte hand-maintained)
4. ~~**Fix `security-routing.md` ADR-090 status**~~ — ✅ **#2077**
5. **Patch `tools/check_str_exc_bus_bound.sh`** regex + add `bootstrap/` to scan roots — closes 2 already-verified false-negatives (socialmedia adapter, dashboard admin RPCs) in one gate change. (security + architecture)
6. **Harden `deploy/quadlet/factory-cloudflared.container`** — add `ReadOnly=true` + pin the image tag (currently floating `:latest`) on the one internet-facing unit missing both. (security)
7. **Fix `useAgentStatus.ts` `rows[0]` selection bug** — small frontend diff, fixes a visibly-wrong online/harness badge in the operator cockpit. (week-subsystem)
8. **Backport the missing NATS ACL grants** (`$JS.API.STREAM.NAMES` + `CONSUMER.INFO`) to telegram/discord-adapter — the fix pattern already exists (same bug was just fixed for the dashboard). (deploy)
9. **Fix `OpsPage.tsx` hardcoded French log-preset labels** — small diff, brings the page in line with the already-shipped react-i18next EN/FR system. (week-subsystem)
10. **Catch up `packages/roxabi-contracts/CHANGELOG.md`** for the 0.11.0 → 0.13.0 bumps — restores the only human-context satellite reviewers get from the auto-merge contracts-bump PR. (contracts)

## Links to domain detail

- [`by-domain/architecture.md`](by-domain/architecture.md) — 9 findings, subscore 60
- [`by-domain/axial-drift.md`](by-domain/axial-drift.md) — 4 findings, subscore 76
- [`by-domain/security.md`](by-domain/security.md) — 13 findings, subscore 15
- [`by-domain/ssot.md`](by-domain/ssot.md) — 22 findings, subscore 40
- [`by-domain/deploy.md`](by-domain/deploy.md) — 7 findings, subscore 33
- [`by-domain/week-subsystem.md`](by-domain/week-subsystem.md) — 17 findings, subscore 35
- [`by-domain/contracts.md`](by-domain/contracts.md) — 2 findings, subscore 90
- [`by-domain/error-async.md`](by-domain/error-async.md) — 5 findings, subscore 58
