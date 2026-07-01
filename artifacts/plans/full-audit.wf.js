export const meta = {
  name: 'roxabi-factory-full-audit',
  description:
    'Whole-project + last-week audit of roxabi-factory: security, architecture, axial drift (ADR-073), SSoT/duplication, deploy (factory + cross-repo). Ground-truth gates -> read-only finders -> adversarial verify -> synthesis report.',
  whenToUse:
    'Pre-release health check / post-busy-week review. Read-only: produces a report under artifacts/analyses/, files no issues, commits nothing.',
  phases: [
    { title: 'Ground truth', detail: 'run existing gates + collect the week delta' },
    { title: 'Find', detail: '~25 read-only finders across 8 domains x repo/week/cross-repo scope' },
    { title: 'Verify', detail: 'adversarial read-only refutation of every critical/high finding' },
    { title: 'Synthesize', detail: 'map-reduce: per-domain synth (parallel) -> meta-synth AUDIT-SUMMARY.md' },
  ],
}

// ----------------------------------------------------------------------------
// Tunables — edit these to dial scope/cost. WEEK_SINCE bounds the "this week"
// delta. Set VERIFY_SEVERITIES to [] to skip the adversarial pass entirely.
// ----------------------------------------------------------------------------
const AUDIT_DIR = 'artifacts/analyses/2026-06-30-full-audit'
const WEEK_SINCE = '2026-06-23'
const VERIFY_SEVERITIES = ['critical', 'high']
const REPO = '~/projects/roxabi-factory'

// ----------------------------------------------------------------------------
// Schemas
// ----------------------------------------------------------------------------
const GROUND_TRUTH = {
  type: 'object',
  additionalProperties: false,
  properties: {
    gates: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        properties: {
          name: { type: 'string' },
          status: { type: 'string', enum: ['pass', 'fail', 'skip', 'error'] },
          detail: { type: 'string' },
        },
        required: ['name', 'status'],
      },
    },
    week_subsystems: { type: 'array', items: { type: 'string' } },
    hotspots: { type: 'array', items: { type: 'string' } },
    notes: { type: 'string' },
  },
  required: ['gates', 'week_subsystems'],
}

const FINDINGS = {
  type: 'object',
  additionalProperties: false,
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        properties: {
          id: { type: 'string' },
          domain: { type: 'string' },
          severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
          title: { type: 'string' },
          file: { type: 'string' },
          line: { type: 'integer' },
          evidence: { type: 'string' },
          ssot_duplicate_of: { type: 'string' },
          recommendation: { type: 'string' },
        },
        required: ['id', 'severity', 'title', 'file', 'evidence', 'recommendation'],
      },
    },
  },
  required: ['findings'],
}

const VERDICT = {
  type: 'object',
  additionalProperties: false,
  properties: {
    verdict: { type: 'string', enum: ['CONFIRMED', 'PLAUSIBLE', 'REFUTED'] },
    reasoning: { type: 'string' },
    adjusted_severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
  },
  required: ['verdict', 'reasoning'],
}

const SYNTH = {
  type: 'object',
  additionalProperties: false,
  properties: {
    debt_score: { type: 'integer' },
    severity_counts: {
      type: 'object',
      additionalProperties: false,
      properties: {
        critical: { type: 'integer' },
        high: { type: 'integer' },
        medium: { type: 'integer' },
        low: { type: 'integer' },
      },
    },
    top_findings: { type: 'array', items: { type: 'string' } },
    quick_wins: { type: 'array', items: { type: 'string' } },
    files_written: { type: 'array', items: { type: 'string' } },
    executive_summary: { type: 'string' },
  },
  required: ['debt_score', 'severity_counts', 'executive_summary'],
}

// Map step output — one compact summary per domain (NO raw findings flow to the
// reduce step, which is what bounds the meta-synth context).
const DOMAIN_SUMMARY = {
  type: 'object',
  additionalProperties: false,
  properties: {
    domain: { type: 'string' },
    severity_counts: {
      type: 'object',
      additionalProperties: false,
      properties: {
        critical: { type: 'integer' },
        high: { type: 'integer' },
        medium: { type: 'integer' },
        low: { type: 'integer' },
      },
    },
    debt_subscore: { type: 'integer' },
    top_findings: { type: 'array', items: { type: 'string' } },
    duplications: { type: 'array', items: { type: 'string' } },
    file_written: { type: 'string' },
    notes: { type: 'string' },
  },
  required: ['domain', 'severity_counts', 'top_findings', 'file_written'],
}

// ----------------------------------------------------------------------------
// Shared rules injected into every read-only finder prompt.
// ----------------------------------------------------------------------------
const READONLY_RULES = `
RULES (read-only audit agent):
- You are READ-ONLY. Do NOT Edit/Write any repo file, do NOT git add/commit/push, do NOT run formatters.
- Working dir = ${REPO} (repo root). Cross-repo paths live under ~/projects/.
- PRIMARY discovery tool = ccc (cocoindex semantic search), then grep for exact/regex. Confirmed syntax: \`ccc search "<natural-language concept>" [--lang python] [--path "src/factory/**"] [--limit N] [--refresh]\`. The index is daemon-watched and fresh. Use ccc to find duplication: search a concept, then cluster results by directory — same concept implemented in 2+ non-shared modules with score > 0.85 = confirmed duplication; 0.7-0.85 = probable (manual confirm). Read only the spans you need (chunked reads, tail long command output).
- Trust code > generated/enforced artifacts > ccc > CLAUDE.md/ADRs (intent only). ~67% doc-rot measured — do not treat prose as SSoT.
- Cite every finding with a real file:line you actually opened. No speculation without a path.
- Severity: critical = exploitable security / data-loss / silent prod outage; high = correctness bug or confirmed axial/SSoT drift forcing rework; medium = refactor/probable drift; low = cleanup.
- Return ONLY via the structured schema. Each finding id MUST be prefixed with your finder key.`

// ----------------------------------------------------------------------------
// Finder matrix. scope: repo | week | cross-repo. agentType controls capability
// (all read-only). Tune freely.
// ----------------------------------------------------------------------------
const FINDERS = [
  // --- Architecture & layering (vs .importlinter + ADRs + CURRENT.generated.md) ---
  {
    key: 'arch-core',
    domain: 'architecture',
    scope: 'repo',
    agentType: 'Explore',
    prompt: `Audit ARCHITECTURE of src/factory/core (177 files / ~19k SLOC). Check: layer-boundary leaks (infrastructure concerns polluting core domain objects per .importlinter clean-architecture-layers), god-modules / oversized packages, intra-core coupling between core/hub, core/ports, core/agent, core/stores, core/memory. Cross-check against docs/architecture/CURRENT.generated.md and the target-architecture.md. Flag dead/unreachable code paths (composition-root never constructs a registered backend — the #1883 pattern).`,
  },
  {
    key: 'arch-adapters',
    domain: 'architecture',
    scope: 'repo',
    agentType: 'Explore',
    prompt: `Audit ARCHITECTURE of src/factory/{adapters,outbound,inbound} (~13k SLOC). Primary check: inbound-layer logic leaking into adapters (ADR boundary), and adapter-level retry/auth/serialization that belongs in transport/. Confirm adapters do not import core directly in violation of the layer contract.`,
  },
  {
    key: 'arch-bootstrap',
    domain: 'architecture',
    scope: 'repo',
    agentType: 'Explore',
    prompt: `Audit ARCHITECTURE of src/factory/{bootstrap,cli} (~9.5k SLOC). Check the 6 production composition roots (hub, adapter telegram/discord/clipool/omp, turn-writer) + unified factory start: every registered/dispatched backend is actually CONSTRUCTED at a bootstrap root (grep composition roots; a backend that is declared but never built is unreachable in prod despite green CI). Flag wiring duplication across the standalone bootstrappers.`,
  },
  {
    key: 'arch-infra',
    domain: 'architecture',
    scope: 'repo',
    agentType: 'Explore',
    prompt: `Audit ARCHITECTURE of src/factory/{infrastructure,nats,transport,streaming} + packages/roxabi-nats. Check: core/stores protocols must not import SQLite drivers (forbidden contract), transport<-streaming<-core layering purity, WorkerPoolClient 3-layer composition (#1278), KV-key sanitization presence on any id used as a NATS-KV key (the to_pool_id colon outage class, #1721).`,
  },

  // --- Axial drift (ADR-073 stage-of-pipeline axis) — purpose-built read-only agent ---
  {
    key: 'axial-repo',
    domain: 'axial-drift',
    scope: 'repo',
    agentType: 'Explore',
    prompt: `Review the WHOLE repo for axial drift against the axial ADR (docs/architecture/adr/073-axial-stage-of-pipeline-decomposition.mdx, axial:true; see also 083). Find cross-cutting concerns duplicated across non-primary-axis siblings (N×M trap) — same logic repeated per platform/target instead of living once on the stage axis. Tag each with target-axis-trap. Use ccc to confirm suspected duplication (similarity > 0.85 = confirmed, 0.7-0.85 = probable).`,
  },
  {
    key: 'axial-week',
    domain: 'axial-drift',
    scope: 'week',
    agentType: 'Explore',
    prompt: `Review ONLY the last week's new code (factory-ingress src/factory/ingress, fleet-obs packages/roxabi-obs + dashboard /fleet, persona-soul src/factory/blobstore + dashboard, dashboard Enishu shell) for axial drift vs ADR-073. New subsystems are the highest drift risk: check that ingress connectors, fleet reporters, and soul handlers are decomposed along the stage axis, not duplicated per integration target/platform.`,
  },

  // --- Security — read-only security-auditor ---
  {
    key: 'sec-ingress',
    domain: 'security',
    scope: 'week',
    agentType: 'Explore',
    prompt: `Security audit of factory-ingress (src/factory/ingress, #2008): webhook receiver for GitHub/Cloudflare -> factory.event.*. Verify HMAC signature verification (verify.py) is constant-time and mandatory, untrusted payloads are validated/narrowed before publish, no SSRF / no payload-driven topic injection, the connector registry cannot be written by an unauthenticated caller (the #1992 trust-inversion: dashboard wizard writing HMAC keys = REJECTED). Check the cloudflared tunnel exposure surface.`,
  },
  {
    key: 'sec-authz',
    domain: 'security',
    scope: 'repo',
    agentType: 'Explore',
    prompt: `Security audit of the agent authorization matrix (ADR-090): AgentAuthorizer / AuthorizeAgentMiddleware / AgentGrantStore in src/factory. Factory is MULTI-user. Verify authz is live-enforced at the hub (idx 7), grants are per-agent and bots inherit, refusal pointers work. Flag the KNOWN-open bugs and confirm/deny they are still live: dead permissions_json field, unwired GuardChain. Check auth.db (grants/identity) vs config.db separation.`,
  },
  {
    key: 'sec-secrets-acl',
    domain: 'security',
    scope: 'repo',
    agentType: 'Explore',
    prompt: `Security audit of secrets + NATS ACL. Check: deploy/secrets-policy.toml <-> required_secrets <-> quadlet.toml parity (gaps the gates miss), nats seed files, _FILE->env boot bridges (the silent-401 class: a worker reading a plain-env key needs explicit _export_secret_file), and SanitizedError discipline — str(exc)/f"{exc}"/repr(exc) must never flow into bus-bound message fields or SanitizedError (ADR-073/#1212; gate check_str_exc_bus_bound). Inspect the acl-matrix -> auth.conf generation for over-broad grants.`,
  },
  {
    key: 'sec-blobstore-soul',
    domain: 'security',
    scope: 'week',
    agentType: 'Explore',
    prompt: `Security audit of persona-soul blobstore (src/factory/blobstore + packages/roxabi-blobs + dashboard agents config UI, #2057-2068). Check: blob access control (can one agent/tenant read another's soul blob?), the soul secret-lint warning actually blocks secrets in persona text, soul preload/cache invalidation on soul.put cannot be poisoned, BlobStore URL (roxabi.network, #2067) is not SSRF-able.`,
  },
  {
    key: 'sec-dashboard',
    domain: 'security',
    scope: 'week',
    agentType: 'dev-core:security-auditor',
    prompt: `Security audit of the dashboard (apps/dashboard/src 66 TS/TSX + src/factory/dashboard 13 py, #2048/#2052/#2065). Check: unauthenticated RPC endpoints, the /fleet and /integrations pages, ops engine URLs, XSS in chat/cockpit rendering, CORS, any write endpoint reachable without authz. The dashboard's intended role is read-only panels — flag any write path.`,
  },

  // --- SSoT / duplication / parallel-path (the "same thing in N places" axis) ---
  {
    key: 'ssot-config',
    domain: 'ssot',
    scope: 'repo',
    agentType: 'Explore',
    prompt: `Find SSoT violations in deploy config. quadlet.toml is the manifest; Makefile quadlet-install, converge.sh, install.sh, secrets-manifest.sh and secrets-policy.toml are hand-enumerated mirrors. Find drift/duplication the existing gates (check_quadlet_manifest_install, check_secrets_drift, check_quadlet_component_source) do NOT cover. Flag any place a container/volume/secret/ACL is declared in 2+ files that can diverge.`,
  },
  {
    key: 'ssot-stores',
    domain: 'ssot',
    scope: 'repo',
    agentType: 'Explore',
    prompt: `Find duplicated logic across src/factory/infrastructure stores and packages. Use ccc: search "retry logic", "serialize", "to_pool_id"/KV-key sanitization, "create-or-open bucket", JSON extraction helpers. Flag identical/near-identical implementations in sibling stores (similarity > 0.85). Report each as ssot with ssot_duplicate_of pointing at the other location.`,
  },
  {
    key: 'ssot-nats',
    domain: 'ssot',
    scope: 'repo',
    agentType: 'Explore',
    prompt: `Find SSoT violations in NATS subject + ACL definitions. Subjects/contracts live in packages/roxabi-contracts and packages/roxabi-nats; ACL grants live in acl-matrix.json which regenerates auth.conf, the 706-spec, v3-current, CURRENT.generated.md AND a hand-mirrored tests/scripts/fixtures/v3-pre-grant-group.json. Map where subject names / grant identities are restated and could diverge. Flag any subject string hardcoded outside the contracts package.`,
  },
  {
    key: 'ssot-docs',
    domain: 'ssot',
    scope: 'repo',
    agentType: 'Explore',
    prompt: `Audit docs SSoT. Compare docs/architecture/CURRENT.generated.md (generated SSoT) vs the <=9 domain pages vs docs/architecture/adr/*.mdx (72 ADRs) vs docs/claude-md-registry.md vs the AGENTS.md/CLAUDE.md shims. Flag: stale counts, method-dumps, symbols referenced in docs but absent from src (doc-drift class), domain pages contradicting CURRENT.generated.md, ADRs whose status frontmatter or redirect banner is missing.`,
  },
  {
    key: 'ssot-ccc-sweep',
    domain: 'ssot',
    scope: 'repo',
    agentType: 'Explore',
    prompt: `Multi-modal ccc duplication sweep — the systematic engine for the "same thing in N places" axis. Run \`ccc search "<concept>" --lang python --limit 12\` for EACH seed concept below, then cluster hits by top-level src/factory/ package. Flag any concept implemented in 2+ non-shared sibling modules (score > 0.85 = confirmed, report ssot_duplicate_of). Seeds: "retry with backoff", "serialize model to JSON for NATS", "sanitize string for NATS KV key", "verify HMAC signature", "authorize agent / check grant", "extract JSON from LLM response", "parse ISO timestamp", "create-or-open JetStream KV bucket", "load secret from _FILE into env", "build outbound message envelope", "wait for hub ready", "render quadlet template", "dedup / idempotency key". Also sweep tsx: \`ccc search "fetch RPC from dashboard" --lang tsx\`. Distinguish a legitimate shared helper (single home, imported) from a copy-pasted reimplementation (the real finding).`,
  },
  {
    key: 'ssot-crossrepo',
    domain: 'ssot',
    scope: 'cross-repo',
    agentType: 'Explore',
    prompt: `Audit cross-repo duplication under ~/projects. The NATS transport SDK (roxabi-nats), contracts (roxabi-contracts), base images (roxabi-container/roxabi-ml-base), and deploy patterns are shared by factory, voiceCLI, llmCLI, imageCLI. Find: copy-pasted deploy logic that should live in ~/projects/deploy.sh or a shared lib, divergent vendored copies of the NATS SDK, and the same Quadlet/secret pattern reimplemented per repo. Read each repo's deploy/quadlet.toml.`,
  },

  // --- Deploy & ops (factory + cluster) — read-only ---
  {
    key: 'deploy-factory',
    domain: 'deploy',
    scope: 'repo',
    agentType: 'Explore',
    prompt: `Audit factory deploy/. Check: converge.sh (M1-only, require_host_role factory-hub), install.sh idempotency, quadlet templates (RestartSec=10 S12, secret target S7, naming S8 per container-deployment-standard 18 standards), factory-quadlet-sync.sh (runs FULL make converge on ANY staging HEAD change — confirm that is still true and intended), and the tiered exit-code handling (#2037/#2050). Flag template-blind every-run installs and any quadlet.toml component with no on-disk unit file.`,
  },
  {
    key: 'deploy-cluster',
    domain: 'deploy',
    scope: 'cross-repo',
    agentType: 'Explore',
    prompt: `Audit the cluster installer at ~/projects: deploy.sh + lib/cluster_plan.py + hosts.toml. Known epic #2034 (axial fault): deploy.sh is a strict cluster-wide all-or-nothing installer that converge hard-depends on under set -e — S2 template-blind every-run, S1 phantom glob (stale worktrees on M1), S3 host_roles=any. Verify current state of those faults, the host-role intersection logic (renaming a role silently orphans repos whose quadlet.toml is on the old role), and that factory-prod stays M1-only.`,
  },
  {
    key: 'deploy-acl-pipeline',
    domain: 'deploy',
    scope: 'repo',
    agentType: 'Explore',
    prompt: `Audit the ACL/auth generation pipeline. ANY acl-matrix.json edit must regenerate 4 artifacts (auth.conf, 706-spec, v3-current, CURRENT.generated.md) AND hand-mirror the publish/subscribe diff into tests/scripts/fixtures/v3-pre-grant-group.json (NOT script-generated; gated CI-only by test_v4_render_set_equals_v3_render). Verify the regen is wired so a partial regen cannot pass local pre-push but fail CI. Note: CI ACL != prod auth.conf, so ACL bugs only surface on M1 deploy — flag get-vs-watch ACL mismatches (#1572 STREAM.MSG.GET vs ephemeral-consumer).`,
  },

  // --- Week subsystem deep-dives (end-to-end correctness of what shipped) ---
  {
    key: 'week-ingress',
    domain: 'week-subsystem',
    scope: 'week',
    agentType: 'Explore',
    prompt: `End-to-end review of factory-ingress (#2008, merged this week). Trace GH/CF webhook -> verify.py -> factory.event.* JetStream -> connector registry -> dashboard integrations page. Check: image builds (publish.yml triggers post-merge only — a broken Containerfile/install passes PR green, fails post-merge), the factory-ingress container is fully wired (quadlet.toml + Makefile + secrets-manifest + CURRENT.generated — #2008 had several omission follow-ups), and reachability (webhooks need cloudflared; endpoint was tailnet-only).`,
  },
  {
    key: 'week-fleet-obs',
    domain: 'week-subsystem',
    scope: 'week',
    agentType: 'Explore',
    prompt: `End-to-end review of fleet container observability (#2065): dashboard /fleet + packages/roxabi-obs + container_report ACL grants + gh-helper. Check correctness of the reporter shutdown, ingest path, fleet_list RPC, catalog path, license-policy allowlist for roxabi-obs, and that the roxabi-obs package boots in prod (CI-green != boots on M1).`,
  },
  {
    key: 'week-soul',
    domain: 'week-subsystem',
    scope: 'week',
    agentType: 'Explore',
    prompt: `End-to-end review of persona-soul blobstore (#2057-2068): agent soul stored in blobstore with persona_json fallback, preloaded at agent config load, hot-reload preload + invalidation on soul.put, dashboard soul editor. Check: soul harness parity (consensus doc flagged centralization), cache-coherency races, the fallback path when blob is absent, and omp pool.acquire system_prompt kwarg plumbing.`,
  },
  {
    key: 'week-dashboard-ux',
    domain: 'week-subsystem',
    scope: 'week',
    agentType: 'Explore',
    prompt: `Review the dashboard UX work (#2048/#2052/#2053/#2054/#2055): Enishu shell, i18n FR, cockpit 3-pane chat, integrations page, Ops OMP harness probe. Focus on TS/TSX quality in apps/dashboard/src: harness online/offline detection correctness, ops engine URL derivation, file-length/folder-size discipline (queue_group_alive was moved for the gate), biome lint debt, and any RPC typing papered over to pass CI.`,
  },

  // --- Contracts / wire-compat + error/async cross-cutting ---
  {
    key: 'contracts-wire',
    domain: 'contracts',
    scope: 'repo',
    agentType: 'Explore',
    prompt: `Audit packages/roxabi-contracts (95 files) for wire-compat hazards. A new required field is wire-breaking intra CONTRACT_VERSION because lock-pinned satellites + JetStream backlogs replay old payloads. For any field added/required this week, enumerate producers-per-direction and stream-persisted models; verify the default-mint shim + explicit-producer + flip-issue pattern was followed. Flag required fields with no migration shim.`,
  },
  {
    key: 'err-async',
    domain: 'error-async',
    scope: 'repo',
    agentType: 'Explore',
    prompt: `Cross-cutting audit of error-handling + async. Find: bare except / except Exception without inline justification (AGENTS.md requires a comment + follow-up issue), swallowed exceptions, blocking calls inside async (requests.*, time.sleep, sync file IO in coroutines), unclosed resources / leaked tasks, and missing create-or-open on hub-side state writes before announce_hub_ready (cold-boot class). Use ccc for "except Exception", "time.sleep", "requests.get".`,
  },
]

// ----------------------------------------------------------------------------
// Prompt builders
// ----------------------------------------------------------------------------
function finderPrompt(f, digest) {
  return `You are finder "${f.key}" (domain=${f.domain}, scope=${f.scope}) in a parallel audit of roxabi-factory.

GROUND-TRUTH DIGEST (from the gate/delta pass — use to focus, not as gospel):
${digest}

YOUR TASK:
${f.prompt}
${READONLY_RULES}`
}

function verifyPrompt(x) {
  return `Adversarially VERIFY this audit finding. Your job is to REFUTE it.

FINDING [${x.id}] severity=${x.severity}
title: ${x.title}
location: ${x.file}${x.line ? ':' + x.line : ''}
evidence: ${x.evidence}
${x.ssot_duplicate_of ? 'claimed duplicate of: ' + x.ssot_duplicate_of : ''}

PROTOCOL:
- Open the cited file:line and the surrounding code yourself. Do NOT trust the evidence string.
- REFUTED if the claim is wrong, already mitigated elsewhere, intentional-and-documented, or unreachable in prod.
- CONFIRMED only if you independently reproduce the problem with a concrete path:line and a failure scenario.
- PLAUSIBLE if real-but-uncertain (default when you cannot fully decide).
- Set adjusted_severity to what the evidence actually supports (may downgrade).
You are READ-ONLY: no Edit/Write, no git, no formatters.`
}

// MAP — one agent per domain. Reads only its own domain's findings, writes the
// per-domain report, returns a compact summary (bounds the reduce step).
function domainSynthPrompt(domain, findings, dir) {
  return `You synthesize the "${domain}" domain of the roxabi-factory audit. ${findings.length} findings for this domain (REFUTED already removed) follow as JSON. Each carries verdict (CONFIRMED/PLAUSIBLE/UNVERIFIED) and adjusted_severity.

FINDINGS JSON:
${JSON.stringify(findings)}

DO THIS:
1. Dedup findings that share a root cause / same file-region (multiple finders may have hit the same thing). Note each merge.
2. Rank by adjusted_severity (fall back to severity). At equal severity: CONFIRMED > PLAUSIBLE > UNVERIFIED.
3. Compute a debt_subscore 0-100 for THIS domain (100 = pristine).
4. WRITE ${dir}/by-domain/${domain}.md (create the dir if needed): severity sections (P0/P1/P2/P3), one row per finding with file:line, verdict, evidence, recommendation; plus a duplication table if this is the ssot/axial domain.
5. Return DOMAIN_SUMMARY: domain, severity_counts, debt_subscore, top_findings (ranked one-liners "sev | file:line | title"), duplications (cross-cutting SSoT/dup notes the reduce step should merge across domains), file_written, notes.
GUARDRAILS: Write ONLY under ${dir}. Do NOT modify any file outside it. Do NOT git add/commit/push. Do NOT run formatters or gates.`
}

// REDUCE — reads ONLY the per-domain summaries (compact), never raw findings.
function metaSynthPrompt(domainSummaries, digest, dir) {
  return `You are the REDUCE step of the roxabi-factory audit. Below are the per-domain summaries (NOT raw findings — the detail already lives in ${dir}/by-domain/*.md). ${domainSummaries.length} domains.

GROUND-TRUTH DIGEST:
${digest}

PER-DOMAIN SUMMARIES JSON:
${JSON.stringify(domainSummaries)}

DO THIS:
1. Merge cross-domain duplicates — a single root cause flagged in 2+ domains (e.g. an SSoT issue also surfaced by security). Use the duplications[] arrays.
2. Compute a global Technical Debt Score 0-100 (weight the domain debt_subscores by severity volume; 100 = pristine).
3. Total the severity_counts across domains.
4. Build a triage table: each P0/P1 item -> suggested GitHub issue title + owning domain (DO NOT create issues — propose only; mutations go through roxabi-issues:issue-triage later).
5. WRITE ${dir}/AUDIT-SUMMARY.md: executive summary; P0/P1/P2/P3 sections (pointing into by-domain/*.md); axial-drift summary table; SSoT/duplication table; deploy section; metrics dashboard (per-domain issues x severity); global debt score; top-10 quick wins (high impact / low effort); triage table; link to each by-domain file.
6. Return SYNTH: debt_score, severity_counts, top_findings, quick_wins, files_written (include the by-domain files you were told about + AUDIT-SUMMARY.md), executive_summary.
GUARDRAILS: Write ONLY under ${dir}. Do NOT modify any file outside it. Do NOT git add/commit/push. Do NOT run formatters or gates.`
}

// ----------------------------------------------------------------------------
// Execution
// ----------------------------------------------------------------------------
phase('Ground truth')
const ground = await agent(
  `Establish ground truth for a roxabi-factory audit. Working dir = ${REPO}.
0. Freshen the semantic index: run \`ccc index\` (incremental, fast) and record the resulting chunk/file count as a gate entry named "ccc-index". This guarantees every downstream finder's ccc search is current.
1. Run the repo quality gates and record pass/fail/skip for each: importlinter (uv run --frozen import-linter or .venv/bin/import-linter), and the tools/check_*.sh / tools/check_*.py gates (doc_drift, doc_semantic_drift, secrets_drift, secrets_source, quadlet_manifest_install, quadlet_component_source, str_exc_bus_bound, hardcoded_constants, file_length, folder_size, test_sleep, omp_pin_lockstep, architecture_snapshot, volumes_table). Run them read-only; capture the failure summary line, not full logs.
2. Collect the last-week delta: git log --since="${WEEK_SINCE}" --oneline | wc -l, and git diff --name-only "$(git rev-list -1 --before='${WEEK_SINCE}' staging)" staging | sed 's|/[^/]*$||' | sort | uniq -c | sort -rn | head -30. Identify the new/heavily-touched subsystems.
3. List hotspots = files/dirs with the largest churn this week.
You are READ-ONLY: run only diagnostic commands, no Write/Edit/git-mutation.
Return via schema: gates[], week_subsystems[], hotspots[], notes.`,
  { label: 'ground-truth', phase: 'Ground truth', agentType: 'Explore', schema: GROUND_TRUTH },
)

const digest = ground
  ? [
      'GATES: ' +
        (ground.gates || []).map((g) => `${g.name}=${g.status}`).join(', '),
      'WEEK SUBSYSTEMS: ' + (ground.week_subsystems || []).join(', '),
      'HOTSPOTS: ' + (ground.hotspots || []).slice(0, 15).join(', '),
      ground.notes ? 'NOTES: ' + ground.notes : '',
    ]
      .filter(Boolean)
      .join('\n')
  : 'ground truth unavailable — proceed from the finder task description.'

log(`Ground truth done. Launching ${FINDERS.length} finders (find + verify pipelined).`)

phase('Find')
const results = await pipeline(
  FINDERS,
  // stage 1 — read-only finder
  (f) =>
    agent(finderPrompt(f, digest), {
      label: `find:${f.key}`,
      phase: 'Find',
      agentType: f.agentType,
      schema: FINDINGS,
    }),
  // stage 2 — adversarial verify of this finder's critical/high findings (pipelined per finder)
  (found, f) => {
    if (!found || !found.findings || !found.findings.length) return []
    const tagged = found.findings.map((x) => ({ ...x, finder: f.key, domain: x.domain || f.domain }))
    const toVerify = tagged.filter((x) => VERIFY_SEVERITIES.includes(x.severity))
    const passthrough = tagged
      .filter((x) => !VERIFY_SEVERITIES.includes(x.severity))
      .map((x) => ({ ...x, verdict: 'UNVERIFIED' }))
    if (!toVerify.length) return passthrough
    return parallel(
      toVerify.map((x) => () =>
        agent(verifyPrompt(x), {
          label: `verify:${f.key}:${x.id}`,
          phase: 'Verify',
          agentType: 'Explore',
          schema: VERDICT,
        }).then((v) => ({
          ...x,
          verdict: v ? v.verdict : 'PLAUSIBLE',
          verify_reasoning: v ? v.reasoning : 'verifier unavailable',
          adjusted_severity: v && v.adjusted_severity ? v.adjusted_severity : x.severity,
        })),
      ),
    ).then((verified) => [...verified.filter(Boolean), ...passthrough])
  },
)

const all = results
  .flat()
  .filter(Boolean)
  .filter((x) => x.verdict !== 'REFUTED')

log(`Find+verify complete: ${all.length} surviving findings (REFUTED dropped). Synthesizing.`)

// Group surviving findings by domain for the map step.
const byDomain = {}
for (const f of all) {
  const d = f.domain || 'misc'
  if (!byDomain[d]) byDomain[d] = []
  byDomain[d].push(f)
}
const domainEntries = Object.entries(byDomain).filter(([, fs]) => fs.length)

phase('Synthesize')
log(`Map-reduce synthesis: ${domainEntries.length} per-domain synth agents -> 1 meta-synth.`)

// MAP — barrier: the meta-synth genuinely needs every domain summary together.
const domainSummaries = (
  await parallel(
    domainEntries.map(([domain, findings]) => () =>
      agent(domainSynthPrompt(domain, findings, AUDIT_DIR), {
        label: `synth:${domain}`,
        phase: 'Synthesize',
        schema: DOMAIN_SUMMARY,
      }),
    ),
  )
).filter(Boolean)

// REDUCE — reads only the compact per-domain summaries.
const summary = await agent(metaSynthPrompt(domainSummaries, digest, AUDIT_DIR), {
  label: 'synth:meta',
  phase: 'Synthesize',
  schema: SYNTH,
})

return {
  audit_dir: AUDIT_DIR,
  finders: FINDERS.length,
  surviving_findings: all.length,
  domains: domainEntries.map(([d, fs]) => `${d}:${fs.length}`),
  gates: ground ? ground.gates : [],
  summary,
}
