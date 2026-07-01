# Axial-drift domain — audit synthesis (2026-06-30)

4 raw findings (2 finders: `axial-repo`, `axial-week`) → **4 deduped** (no exact file-region collisions; one related-but-distinct cluster flagged below, not merged — see Duplication table). Ranked by `adjusted_severity` (fallback `severity`); ties broken CONFIRMED > PLAUSIBLE > UNVERIFIED.

## Severity counts (deduped)

| Severity | Count |
|---|---|
| critical | 0 |
| high | 0 |
| medium | 2 |
| low | 2 |

**debt_subscore: 76/100** (100=pristine). No critical/high axial-boundary violations this cycle. The lead finding (#1) is the only fully CONFIRMED item — a real, independently-verified triplicated `_validate_worker_error()` helper across the claude/omp/cli_pool codec stage, with one of the three copies already dead code left over from a migration. The remaining three findings are UNVERIFIED (not independently re-checked this pass) but evidence-backed pattern matches consistent with this repo's documented "target-axis-trap" failure mode (ADR-073): cross-cutting concerns (platform-identity rendering, a hub accessor helper, missing per-subsystem stage-axis docs) re-implemented or omitted per call-site instead of factored onto a shared axis.

---

## P0 — Critical

None.

## P1 — High

None. (`axial-repo-001` was originally filed as `high` but adjusted to `medium` on verification — see row below; behaviorally identical triplicate today, no live divergence, prospective risk only.)

---

## P2 — Medium

| # | File:line | Verdict | Title | Evidence (condensed) | Recommendation |
|---|---|---|---|---|---|
| 1 | `src/factory/llm/claude_job_codec.py:24` (+ `omp_job_codec.py:18`, `cli_pool_codec.py:41`) | CONFIRMED | `_validate_worker_error()` triplicated near-verbatim across 3 sibling codec modules instead of living once on the stage axis | Independently re-verified: all three functions present at cited lines, 12/13 lines byte-identical (only the log-prefix string differs). `ClaudeJobCodec` (imported `drivers/claude_rpc.py:21`) and `OmpJobCodec` (imported `drivers/omp_rpc.py:24`) are both live/wired in bootstrap (`hub_assembly.py`, `unified.py`, `providers.py`). `CliPoolCodec` confirmed dead — its only importer outside itself is `tests/llm/test_cli_pool_codec.py`; the live NATS clipool path uses `CliNatsCodec` instead. Commit `1c8d18ad` (2026-06-28, "unify claude-cli on JobEnvelope pub/sub phase 2") added `claude_job_codec.py` by copying the existing `omp_job_codec.py` pattern rather than extracting it — the very PR that unified transports re-introduced the duplication at the validation layer. ADR-073 explicitly names this failure mode ("a shared helper changes nothing structural... the next concern re-creates the duplication") and project precedent treats 3 sibling copies as the actionable threshold. No functional divergence today — all 3 copies share identical `KNOWN_CODES` guard + `worker.internal` fallback — so risk is prospective (future fix-one-miss-others), not a live bug; downgraded high→medium on that basis. | Hoist `_validate_worker_error` (KNOWN_CODES guard + fallback + logging) into one shared stage-axis module (e.g. `roxabi_contracts.errors` or new `factory.llm._codec_common`); parameterize only the log-prefix label. Delete orphaned `cli_pool_codec.py` (`CliPoolCodec`, unused since the JobEnvelope migration) or document why it's intentionally retained. |
| 2 | `apps/dashboard/src/components/agents/AgentsListPanel.tsx:287` (+ `:382`, `admin_rpc.py:90`, `dashboard_agents_rpc.py:53`, `AdminPage.tsx:146`) | UNVERIFIED | Telegram/Discord/Email presence rendering hardcoded field-by-field across 5 sites instead of iterating one platform list (target-axis-trap) | All 5 sites introduced/expanded by this week's PR #2070 (dashboard admin users/agents). `AgentsListPanel.tsx` duplicates the identical 3-badge block twice in the same file (card view :285-296, table view :382-389). Contract itself (`packages/roxabi-contracts/src/roxabi_contracts/dashboard/models.py:11`) declares `PlatformTag = Literal["telegram","discord","web"]` but `DashboardAgentSummary` only carries `has_telegram`/`has_discord`/`has_email` — `web` has no corresponding flag anywhere, supporting the claim that adding a platform requires touching every hardcoded site. ccc search corroborates structural similarity (0.66/0.62, probable band) but this row was not independently re-verified this pass (no direct diff/import-graph check beyond the original finder's evidence). | Introduce one canonical `SUPPORTED_PLATFORMS` list (driven by `PlatformTag`) + a single shared `<PlatformBadges>` component + one backend `platform_identities_for(...)` helper consumed by both `admin_rpc.py` and `dashboard_agents_rpc.py`. Decide whether `web` is a real identity type and wire it through everywhere or drop it from `PlatformTag`. |

---

## P3 — Low

| # | File:line | Verdict | Title | Evidence (condensed) | Recommendation |
|---|---|---|---|---|---|
| 3 | `src/factory/bootstrap/factory/dashboard/admin_rpc.py:45` (+ `dashboard_agents_rpc.py:41`) | UNVERIFIED | `_agent_store(hub)` helper byte-identically duplicated across two dashboard RPC modules split out of `dashboard_rpc.py` for the file-length gate | 6-line function (`getattr(hub, "_agent_store", None)` → `next(iter(hub.agent_registry.values()), None)` fallback) claimed byte-identical via `diff` in both modules; both born from this week's PR #2070 file-length split. Unlike this finding, the split kept `_blob_store`/`_bot_store`/`_user_store`/`_grant_store` single-defined — so this is plausibly a one-off miss in an otherwise-correct extraction, not a systemic pattern. Not independently re-verified this pass. | Move `_agent_store(hub)` into a shared dashboard RPC helpers module (e.g. `dashboard/_hub_accessors.py`) imported by both `admin_rpc.py` and `dashboard_agents_rpc.py`, mirroring how the other hub-accessor helpers are kept single-defined. |
| 4 | `src/factory/ingress/:1` | UNVERIFIED | New `factory.ingress` subsystem (ADR-096, this week's highest-churn new pipeline) has no AGENTS.md/CLAUDE.md documenting the stage-axis invariant (shared verify/normalize/payload logic vs. per-connector thin config) | `find src/factory/ingress -iname '*.md'` returns nothing per the finder; every comparable subsystem (`blobstore/AGENTS.md`, `nats/AGENTS.md`, `roxabi-obs/AGENTS.md`) carries one. Low risk today since current code reportedly already follows the correct pattern (shared `verify.py`/`normalize.py`/`payload.py`, thin `connectors/*.py`) — the gap is the missing guardrail for the *next* connector (e.g. planned Vercel connector), not a present violation. Not independently re-verified this pass (doc-existence claim only, not cross-checked against current connector code for drift). | Add `src/factory/ingress/AGENTS.md` stating the Connector protocol contract (verify/parse_external_id/apply_lifecycle/normalize stay thin per-target; shared stage logic lives in verify.py/normalize.py/payload.py). |

---

## Duplication table (cross-domain notes for reduce step)

| Cluster | Members | Same root cause? | Note |
|---|---|---|---|
| Dashboard PR #2070 hub-accessor/identity split | #2 (`apps/dashboard/.../AgentsListPanel.tsx`, `admin_rpc.py:90`, `dashboard_agents_rpc.py:53`) and #3 (`admin_rpc.py:45`, `dashboard_agents_rpc.py:41`) | Related, not identical — both stem from this week's `dashboard_rpc.py` file-length-gate split (PR #2070) leaving cross-cutting concerns (platform-identity rendering vs. hub-accessor helper) un-consolidated in the same two modules, but they touch different functions/line-ranges and different defect shapes (N-platform enumeration vs. simple 1-helper duplication). **Not merged** — kept as separate findings; flagged here so the reduce step can note "PR #2070's dashboard-RPC split systematically skipped extracting shared helpers" as one cross-cutting root cause if other domains (e.g. ssot, contracts) hit the same admin_rpc.py/dashboard_agents_rpc.py files this cycle. |
| Codec-stage triplication (#1) | `claude_job_codec.py`, `omp_job_codec.py`, `cli_pool_codec.py` | Single root cause, single finding | Already one finding (#1 above) — listed here only because it is the domain's clearest "target-axis-trap" exemplar per ADR-073 and may be useful as a canonical example if the reduce step writes a cross-domain "target-axis-trap" pattern summary. |

ADR-073 ("Axial Stage-of-Pipeline Decomposition") is the architecture-doctrine SSoT this whole domain is graded against: it explicitly predicts that a single shared-helper fix without an owning stage-axis module just relocates the duplication, and treats 3 sibling copies of cross-cutting logic as the actionable "three-strikes" threshold. Finding #1 is a textbook hit on that threshold; #2-#4 are weaker (UNVERIFIED) instances of the same class — N call-sites re-declaring a platform enum, a duplicated accessor, and a missing stage-boundary doc.
