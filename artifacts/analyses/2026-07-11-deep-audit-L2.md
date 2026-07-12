# roxabi-factory — Deep Audit L2 (2026-07-11)

| | |
|---|---|
| **Type** | Full deep audit (L2) — re-baseline + delta vs 2026-06-30 |
| **Date** | 2026-07-11 |
| **Repo / branch** | `roxabi-factory` @ `staging` (`48458f5d8`) |
| **Prior baseline** | `artifacts/analyses/2026-06-30-full-audit/` (debt 38/100, 5 P0) |
| **Method** | Ground-truth gates → debt portfolio → trust / axis / deploy / obs slices → prior-P0 re-verify |
| **Evidence** | `artifacts/quality-debt-report.json` (regenerated this run) · `uv run lint-imports` · `scripts/qg` deploy+ACL+hygiene gates · hand reads on BFF/OMP |

---

## 0. Verdict

| | |
|---|---|
| **Promote readiness** | **CONDITIONAL GO** for stage-axis + deploy integrity · **NO-GO** on control-plane authz hardening still open |
| **Gates** | Deploy / ACL / doc-drift / debt-expiry / importlinter / str_exc_bus_bound — **GREEN** |
| **CI (staging sample)** | Recent runs **success** (incl. post-#2281) |
| **Debt score (re-estimate)** | **52 / 100** (↑ from 38) — gates + remediations improved deploy/ACL/OMP; control-plane BFF auth gap **still open** as #2262; quality-debt hygiene regressed (stale tags + 251 untagged) |
| **Top action** | Close or re-scope #2262 (BFF belt-and-suspenders + hub path trace) then hygiene debt pass |

**Not a multi-agent 77-finder rerun.** This is an operator L2 with executable evidence. Prior domain shards remain historical; this file is the 2026-07-11 truth for go/no-go and drain.

---

## 1. Executive summary

Since 2026-06-30:

| Area | Status |
|------|--------|
| Deploy / secrets / volumes / quadlet | **Healthy** — all checked gates OK |
| NATS ACL matrix / grants / subjects | **Healthy** — scanners green |
| Importlinter stage axis (15 contracts) | **Healthy** — 15 kept, 0 broken |
| File/folder size exemptions | **Drained** (registry) — lists empty under SLOC@300 |
| OMP persona apply (`set_system_prompt`) | **Fixed** → `append_system_prompt` at construct |
| Dashboard BFF admin/agents/jobs auth | **Still open** — `require_operator` only on connectors; open as #2262 (reframed defense-in-depth, not confirmed IDOR) |
| NATS wire TLS | **Open** P3 #2264 (posture, not bug) |
| Quality-debt registry hygiene | **Regressed** — 34 stale `DEBT:boundary-broad-catch` after drain; INDEX out of sync; 251 untagged suppressions |

**Bottom line:** production topology and stage-axis enforcement are in good shape. Residual risk is concentrated in (1) control-plane BFF defense-in-depth, (2) honest-debt bookkeeping drift, (3) bootstrap/wiring complexity residual, (4) known network posture (NATS plaintext on LAN).

---

## 2. Gate scoreboard (this run)

| Gate / check | Result | Notes |
|--------------|--------|-------|
| `secrets_drift` | OK | a–d bidirectional |
| `secrets_source` | OK | local vault present |
| `volumes_table` | OK | |
| `quadlet_manifest_install` | OK | |
| `acl_matrix_retired` | OK | |
| `request_reply_flows` | OK | 9 flows |
| `acl_grants` | OK | 4 resources |
| `inbox_prefix` | OK | |
| `subject_literals` | OK | |
| `lint-imports` (importlinter) | **15 kept / 0 broken** | 25 `ignore_imports` residual |
| `debt_expiry` | OK | 6-month window |
| `doc_drift_bundle` | OK | 0 new + semantic + agents_no_adr |
| `str_exc_bus_bound` | OK | no bus-bound `str(exc)` |
| `file_length` / `folder_size` | OK | 1 file at SLOC cap edge: `simple_agent.py` **300** |
| `architecture_snapshot` | regenerated | `CURRENT.generated.md` |

Not re-run this session (CI green recently): full `tests` suite, Playwright e2e, docker-build. Rely on GH Actions sample for those.

---

## 3. Prior P0 re-verification (2026-06-30 → now)

| ID (June) | Finding | July-11 status | Evidence |
|-----------|---------|----------------|----------|
| P0-1 | Dashboard BFF admin/agents **zero** `require_operator` | **OPEN** → #2262 (P2-medium, reframed) | `bff_admin.py`, `bff_agents.py` still no `Depends(require_operator)`; `connectors.py` has it |
| P0-2 | `/api/bff/jobs/steer` no auth / ownership | **OPEN** (same cluster as #2262) | `bff_jobs.py` steer route; hub `handle_jobs_steer` publishes text |
| P0-3/4 | ACL telegram/discord STREAM.NAMES | **CLOSED** (gates green + prior remediations) | `acl_grants` / matrix scanners OK |
| P0-5 | OMP `set_system_prompt` silent no-op | **CLOSED** | `omp_pool.py` uses `append_system_prompt` at RpcClient construction |
| cluster_plan prune | role-rename hazard | **CLOSED** (prior note ROLE_GUARD) | cross-repo; not re-opened |

**Important nuance (#2262):** June called P0 IDOR/privesc. July issue body corrects: hub re-auth via `middleware_authz` (ADR-090) on inbound agent path. BFF gap is **plausible defense-in-depth**, not confirmed IDOR. **Action still required:** end-to-end trace BFF → hub RPC → authz before claiming closed.

---

## 4. Domain slices

### 4.1 Trust / security

| Finding | Sev | Status | Evidence / action |
|---------|-----|--------|-------------------|
| BFF control-plane routes without in-route operator guard | **P1** | Open #2262 | Trace hub path; add `Depends(require_operator)` belt-and-suspenders if hub already guards |
| Jobs steer mutation surface | **P1** | Open (cluster #2262) | Ownership + operator auth on BFF + hub |
| NATS no TLS, 4222 on 0.0.0.0 | **P3** | Open #2264 | Decide TLS-in-container vs Tailnet-only bind |
| Secrets model (Podman `/run/secrets`, policy TOML) | — | Healthy | credentials loader + secrets-policy SSoT |
| Agent USE grants (ADR-090 stage 7) | — | Live | `AuthorizeAgentMiddleware` deny-safe + audit drop |
| `except Exception` BLE001 | **P2** | Partial | 45 BLE001 rows; 35 tagged to **drained** slug (stale); **8 untagged in src** + 2 tools |
| Hardcoded secret literals (heuristic) | — | No smoking gun | tokens via secret paths / env |

**Untagged BLE001 (src) — re-tag or narrow:**

| Path | Note |
|------|------|
| `adapters/clipool/clipool_worker.py:217` | noqa BLE001 sans DEBT / boundary: |
| `bootstrap/factory/dashboard/admin_rpc.py:157` | best-effort rollback |
| `bootstrap/pipeline_ingest.py:111,135` | best-effort |
| `core/pool/pool_processor.py:74,97` | side-channel |
| `dashboard/jobs_stream.py:62` | surface hub errors |
| `dashboard/pipeline_stream.py:53` | surface hub errors |

Steady-state contract from `boundary-broad-catch.md`: ≤30 acknowledged `except Exception` with `# boundary:`. **Reality: drained slug + 34 stale refs + new untagged sites → bookkeeping broken, not necessarily a security explosion.**

### 4.2 Stage axis / architecture

| Signal | Result |
|--------|--------|
| Primary axis contracts | 15/15 KEPT |
| `ignore_imports` residual | **25** — ADR-048 TYPE_CHECKING core→infra (8), shared inbound pipeline→inbound (3), formatters→`outbound._reasoning_accum` (2), stream processor→streaming (6), transport→`_result` (3), nats pipeline sqlite (1) |
| Adapters shape | thin-ish + shared: `adapters/shared/inbound/*`, `_base_outbound`, formatters per platform |
| Parallel platform files | telegram/discord/web still have `*_formatter`, `*_outbound`, `*_inbound` — expected denorm surface, not N×M logic if shared path used |
| `class *Client` in adapters | 1 (`PostizPublicApiClient`) — no Client×Client explosion |
| Large composition roots | `hub_standalone.py` 357 LOC raw / ~273 SLOC; `bootstrap_wiring.py` 316 LOC raw |
| Cap edge | `simple_agent.py` **300 SLOC** — next line fails file_length |

**Axis residual (P2, not gate-breaking):**

1. **importlinter-adr048-transition** still open (core TYPE_CHECKING → infra stores) — 8 ignores
2. **importlinter-shared-modules-transitive** — pipeline/shared peer edges
3. Formatter imports of `outbound._reasoning_accum` (private helper) — ignore + potential public-surface debt
4. Stream processor still couple to streaming state machine via ignore (core↔streaming edge)

### 4.3 Composition roots / deploy topology

| Component class | Notes |
|-----------------|-------|
| Enabled by default | hub, nats, telegram, discord, dashboard, clipool, gh-helper, turn-writer, blobstore, omp, socialmedia, ingress, cloudflared, loki, promtail, log-monitor, otel, litellm-proxy, xai/fw forwarders |
| `disabled = true` | full Langfuse stack + `otel-collector` (ADR-097 deferred) — matches AGENTS.md |
| Standalone bootstrap | `hub_standalone`, `adapter_standalone`, `worker_standalone`, audio consumer helpers |
| CLI | `factory hub` · `adapter {telegram,discord,web,clipool,omp}` · blobstore · ingress · ops · secrets · agent/bot |

**Finding (P3):** docs/AGENTS say Langfuse + otel-collector ship disabled — **verified** in `deploy/quadlet.toml`. Topology narrative and gates aligned.

### 4.4 Quality debt portfolio

| Metric | Value |
|--------|------:|
| Total suppression rows | 408 |
| DEBT-tagged | 157 |
| UNTAGGED | **251** (61%) |
| Stale DEBT refs | **34** (all `boundary-broad-catch` → status drained) |
| Open registry slugs | 15 |
| Drained registry slugs | 3 (`boundary-broad-catch`, `file-exemptions`, `folder-exemptions`) |
| INDEX vs registry drift | **3 rows still "open" in INDEX** |
| Age of open slugs | ~59–61 days (created 2026-05-11…13) — within 6m expiry |

**UNTAGGED hotspots (top rules):**

| Rule | N | Meaning |
|------|--:|---------|
| PLR0913 | 66 | too many args — wiring/bootstrap smell |
| E501 | 25 | line length |
| E402 | 20 | import not at top |
| SLF001 | 19 | private member access |
| arg-type | 18 | pyright |
| C901 | 17 | complexity |
| BLE001 | 10 | broad catch untagged |

**UNTAGGED by package (top):** bootstrap 44 · adapters 34 · core 22 · scripts 19 · infrastructure 17 · llm 16 · nats 15

**DEBT by slug (live counts):**

| Slug | N | fix_class (registry) |
|------|--:|----------------------|
| boundary-broad-catch | 35 | drained (stale!) |
| wiring-bootstrap-deps | 31 | large |
| complexity-residual | 23 | needs_review |
| defensive-narrow-payloads | 15 | medium |
| typer-default-option | 10 | small |
| re-export-init | 9 | small |
| importlinter-adr048-transition | 8 | medium |
| migration-sequence-bootstrap | 6 | large |

### 4.5 Observability / ops audit

| Plane | State |
|-------|--------|
| ① events / ② jobs / ③ infra / ④ read models | Documented ADR-091 living page |
| Operator audit JSONL | path documented; 3-channel ADR-093 |
| Domain audit JetStream | `FACTORY_AUDIT` subjects documented |
| log-monitor | temporary plane-③ pull net (#2245) — retire when Monitoring v2 |
| Sentinelle | design not implemented |
| Dashboard as sole human surface | ADR-092 — open UX epic #2223 |

**Finding (P3):** Observability architecture healthy on paper; Sentinelle/Monitoring v2 still aspirational — not a promote blocker.

### 4.6 Dashboard / UI

| Item | Sev | Ref |
|------|-----|-----|
| UX-hardening epic | P1 epic | #2223 (+ children 2225–2231) |
| Astryx component adapter | P1 | #2228 |
| Prior UX audit | — | `2026-07-03-dashboard-uxui-audit.claude.md` |

Out of scope for binary promote; track as product backlog.

### 4.7 Data paths

| Store | Role | Sync |
|-------|------|------|
| `config.db` / `auth.db` / `turns.db` | app vault | Syncthing |
| `nkeys/`, `env/` | identity / quadlet env | Syncthing |
| `blobstore.tok`, `blobstore/`, jetstream | host-local | excluded |
| `~/.local/state/factory/logs/operator.log` | operator audit | host-local |

No new drift found vs `docs/data-dirs.md` in this pass.

---

## 5. Findings table (deduped, this audit)

| ID | Sev | Domain | Finding | Evidence | Action |
|----|-----|--------|---------|----------|--------|
| F-01 | **P1** | trust | BFF admin/agents/jobs lack in-route `require_operator` | routes vs connectors; #2262 | Trace hub RPC authz → add Depends or document trust model |
| F-02 | **P1** | trust | jobs/steer mutation without BFF operator + ownership check | `bff_jobs.py`, `dashboard_jobs_rpc.py` | Fold into #2262 or child issue |
| F-03 | **P2** | debt | 34 stale `DEBT:boundary-broad-catch` after drain | quality-debt-report stale_references | Retag as `# boundary:` steady-state or re-open slug with sites list |
| F-04 | **P2** | debt | INDEX.md lists 3 drained slugs as open | INDEX vs registry frontmatter | Sync INDEX (hand-maintained) |
| F-05 | **P2** | debt | 251 untagged suppressions (61%) | audit_quality_debt | Classify: tag DEBT: or remove; prioritize BLE001/C901/PLR0913 in bootstrap |
| F-06 | **P2** | axis | 25 importlinter ignores (ADR-048 + stage edges) | `.importlinter` | Drain `#1163` / ports for agent_store + reasoning_accum public API |
| F-07 | **P2** | arch | `wiring-bootstrap-deps` + `migration-sequence-bootstrap` large | 31+6 DEBT rows | Keep async-pipeline drain; no new bootstrap god modules |
| F-08 | **P2** | arch | `simple_agent.py` at SLOC 300 cap | radon | Split before next feature |
| F-09 | **P3** | trust | NATS plaintext LAN | #2264 | Decision: TLS path or bind restrict |
| F-10 | **P3** | obs | Sentinelle / Monitoring v2 not implemented | observability.md | Track epic; log-monitor temporary |
| F-11 | **P3** | ui | Dashboard UX systemic debt | #2223 | Product track |
| F-12 | — | deploy | Secrets/ACL/quadlet | gates OK | Maintain |

---

## 6. Go / no-go

### Promote staging → main

| Criterion | Gate |
|-----------|------|
| importlinter / stage axis | **GO** |
| Deploy secrets / ACL / volumes | **GO** |
| Doc operational drift | **GO** |
| Control-plane authz story closed | **NO-GO** until #2262 traced + decision recorded (fix or accepted residual with Tailnet-only binding + hub enforcement proven) |
| Debt hygiene | **SOFT NO-GO** for “clean promote” narrative — does not block runtime if accepted |

**Recommended promote bar:**

1. #2262: complete path trace; either land `require_operator` on mutating BFF routes **or** ADR/domain-page “Tailnet + hub authz” acceptance with automated test that hub rejects unauthenticated control messages.
2. F-03/F-04: one hygiene PR (stale tags + INDEX).
3. Optional: tag untagged BLE001 in src (F-05 partial).

### Runtime M₁

- Deploy gates green on this machine (secrets present).
- Re-check operator.log + FACTORY_AUDIT on next converge (ops, not code).
- #2264 remains accepted posture until decision.

---

## 7. Drain plan (30 days)

| Week | Work | Closes |
|------|------|--------|
| **W1** | #2262 end-to-end trace + decision PR (Depends or accepted residual + test) | F-01, F-02 |
| **W1** | Hygiene PR: strip/retag 34 stale boundary-broad-catch; fix INDEX 3 rows | F-03, F-04 |
| **W2** | Tag or remove top untagged BLE001 (8 src) + PLR0913 in bootstrap (sample 20) | F-05 |
| **W3** | ADR-048 ignore burn-down spike: agent_store port only (subset of 8) | F-06 partial |
| **W4** | Split `simple_agent.py` before growth; pick one large wiring-bootstrap slice | F-07, F-08 |
| Backlog | #2264 TLS decision · #2223 UX · Monitoring v2 | F-09–F-11 |

**Do not** open a second debt registry system — use existing `artifacts/debt/` + `audit_quality_debt.py`.

---

## 8. Score model (transparent)

| Domain | Subscore (100=clean) | Weight | Rationale |
|--------|---------------------:|-------:|-----------|
| architecture / axis | 78 | 15 | importlinter green; ignores residual |
| axial-drift | 80 | 10 | no Client×Client; shared inbound path |
| security / trust | 45 | 30 | BFF still open; NATS TLS P3; grants live |
| debt hygiene | 40 | 15 | 251 untagged + 34 stale + INDEX drift |
| deploy / ACL | 90 | 15 | all scanners green |
| observability | 70 | 5 | living docs; Sentinelle missing |
| UI | 55 | 5 | epic open |
| contracts / packages | 85 | 5 | gates + packages layout stable |
| **Weighted** | **≈52** | 100 | |

Δ vs June 38: +14 from deploy/ACL/OMP remediations and clearer trust reframing; security still caps the score.

---

## 9. Reproduce

```bash
# debt
uv run python tools/audit_quality_debt.py --root . --out artifacts/quality-debt-report.json

# axis
uv run lint-imports

# deploy + ACL + hygiene (sample)
scripts/qg run secrets_drift secrets_source volumes_table quadlet_manifest_install
scripts/qg run acl_matrix_retired request_reply_flows acl_grants inbox_prefix subject_literals
scripts/qg run debt_expiry doc_drift_bundle str_exc_bus_bound file_length folder_size

# full local parity (heavier)
make qg
# or
scripts/qg run --stage ci
```

Prior full multi-agent audit (historical): `artifacts/analyses/2026-06-30-full-audit/`.

---

## 10. Out of scope / limits

- Full pytest + e2e + docker-build not re-executed locally (CI sample used).
- No live M₁ journald / operator.log tail in this session.
- No adversarial multi-agent finder swarm (June style) — would re-find many closed items; prefer this L2 + targeted issue work.
- Dashboard visual audit not re-run (see 2026-07-03 UX audit).
- `architecture_snapshot` regenerates `CURRENT.generated.md` — verify before commit if dirty.

---

## 11. Next (operator)

1. **Decide** #2262 path (trace → Depends vs accept residual).
2. **PR hygiene** F-03+F-04 (half-day).
3. Optional: schedule next L2 in **2026-10** or before next promote wave.
4. If multi-agent re-audit desired: reuse `artifacts/plans/full-audit.wf.js` against this baseline.

---

*Generated 2026-07-11 · sha `48458f5d8` · branch `staging`*
