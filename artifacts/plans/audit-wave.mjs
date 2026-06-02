// Workflow: audit-wave — the AUDIT-side analogue of epic-1662-wave.mjs.
//
// Reconstructed from artifacts/audits/2026-06-02-quality-audit/manifest.json — the deep
// multi-agent quality audit (69 agents, 20 waves, 8 domains) whose ORCHESTRATION script was
// never persisted (only its output artifacts were). This re-creates a re-runnable harness with
// the same shape so the deep audit is reproducible, exactly as the remediation now is.
//
// Shape (mirrors manifest.waves):
//   Wave 1      — AXIAL foundation: 3 cross-domain invariant checks (import-linter · cocoindex · axial-adr-review)
//   Waves 2-19  — DOMAIN audits: 8 domains × partitions, read-only fan-out → one findings report per (domain,partition)
//   Wave 20     — SYNTHESIS: aggregate every partition report → AUDIT-SUMMARY.md (+ manifest)
//
// READ-ONLY w.r.t. source: every auditor ANALYSES the codebase and writes ONLY its own findings
// report under OUT/<domain>/<partition>.md — it never modifies src/. (Inverse polarity of the
// remediation wave, which writes code. Same fan-out + barrier-before-synthesis shape.)
//
// Invoke: Workflow({ scriptPath: ".../audit-wave.mjs", args: { outDir: "artifacts/audits/2026-06-02-quality-audit" } })
//   args.outDir   — where reports land (pass a DATED dir; Date.now() is unavailable in-script).
//   args.domains  — optional subset of domain keys to re-audit (default: all 8).

export const meta = {
  name: 'audit-wave',
  description: 'Reproducible deep quality audit: axial-invariant baseline → 8-domain × partition read-only fan-out → synthesis into AUDIT-SUMMARY. Read-only w.r.t. source; reconstructed from the 2026-06-02 run manifest.',
  phases: [
    { title: 'Axial', detail: 'import-linter + cocoindex + axial-adr-review — invariant baseline first' },
    { title: 'Domains', detail: '8 domains × partitions, read-only fan-out → per-partition findings reports' },
    { title: 'Synthesis', detail: 'aggregate all partition reports → AUDIT-SUMMARY.md' },
  ],
}

const REPO = 'Roxabi/roxabi-factory'

// ---------- args ----------
let _a = args
if (typeof _a === 'string') { try { _a = JSON.parse(_a) } catch (e) { /* leave */ } }
const OUT = (_a && _a.outDir) || 'artifacts/audits/quality-audit-rerun'
const ONLY = _a && Array.isArray(_a.domains) ? new Set(_a.domains) : null

// ---------- audit taxonomy (partitions verbatim from the 2026-06-02 manifest arbo) ----------
// Partitions bound per-agent context: each Pk/Tk is one disjoint slice of the domain's file set
// (cluster-by-domain, then partition the large file set — see global-patterns §6). P = production
// code slice · T = test slice (code-smells carries both; test-quality is all-T).
const P8 = ['P1', 'P2', 'P3', 'P4', 'P5', 'P6', 'P7', 'P8']
const T6 = ['T1', 'T2', 'T3', 'T4', 'T5', 'T6']
const DOMAINS = [
  { key: 'architecture', parts: P8, focus: 'layer/axis boundaries, ADR adherence, dependency inversion, driven-port purity, composition-root "orchestration-only" discipline' },
  { key: 'async-patterns', parts: P8, focus: 'task lifecycle & cancellation, gather/timeout correctness, blocking calls inside async, races, unawaited coroutines' },
  { key: 'code-smells', parts: [...P8, ...T6], focus: 'god methods (C901/PLR0915), long parameter lists, duplication, dead code, deep nesting; T-slices = test smells' },
  { key: 'error-handling', parts: P8, focus: 'broad `except Exception`, swallowed errors, ack-on-failure (silent message/turn loss), missing re-raise, bare except' },
  { key: 'security', parts: P8, focus: 'OWASP top-10, secret/credential exposure, untrusted-input handling, authz gaps, injection surfaces' },
  { key: 'tech-debt', parts: P8, focus: 'DEBT: markers without issue link, TODO/FIXME without owner, deprecated APIs, loose version pins, transitional shims left active' },
  { key: 'test-quality', parts: T6, focus: 'do tests PROVE behaviour (not just pass)? acceptance-criteria coverage, sleep()-based flakiness, over-mocking, fakes leaking into prod paths' },
  { key: 'type-safety', parts: P8, focus: '`Any` escape hatches, missing annotations, concrete-class typing where a Protocol exists, unchecked casts, `# type: ignore` without reason' },
]
const AXIAL_CHECKS = [
  { key: 'importlinter-report', focus: 'run `uv run lint-imports`; enumerate every import-linter contract and report kept/broken with the offending edge' },
  { key: 'cocoindex-confirmations', focus: 'cross-domain semantic consistency: do architecture ADRs, security specs and deployment docs agree with the code (credential delivery, identity, secret plane)?' },
  { key: 'axial-adr-review', focus: 'primary-axis (STAGE) drift; inbound→adapters leaks; N×M duplication across non-primary-axis siblings' },
]

// ---------- schemas ----------
const SEV = { type: 'string', enum: ['P0', 'P1', 'P2', 'P3'] } // P0 critical · P1 high · P2 medium · P3 low
const FINDINGS_SCHEMA = {
  type: 'object', additionalProperties: false, required: ['domain', 'partition', 'reportPath', 'counts', 'findings'],
  properties: {
    domain: { type: 'string' }, partition: { type: 'string' },
    reportPath: { type: 'string' }, filesScanned: { type: 'number' },
    counts: { type: 'object', additionalProperties: false, properties: { P0: { type: 'number' }, P1: { type: 'number' }, P2: { type: 'number' }, P3: { type: 'number' } } },
    findings: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false, required: ['severity', 'file', 'title', 'detail'],
        properties: {
          severity: SEV, file: { type: 'string' }, line: { type: 'string' },
          title: { type: 'string' }, detail: { type: 'string' }, recommendation: { type: 'string' },
        },
      },
    },
  },
}
const AXIAL_SCHEMA = {
  type: 'object', additionalProperties: false, required: ['check', 'reportPath', 'kept', 'broken'],
  properties: {
    check: { type: 'string' }, reportPath: { type: 'string' },
    kept: { type: 'number' }, broken: { type: 'number' }, notes: { type: 'string' },
  },
}
const SUMMARY_SCHEMA = {
  type: 'object', additionalProperties: false, required: ['summaryPath', 'debtScore', 'filesAudited', 'totals', 'domainBreakdown', 'top'],
  properties: {
    summaryPath: { type: 'string' }, debtScore: { type: 'number' }, filesAudited: { type: 'number' },
    totals: { type: 'object', additionalProperties: false, properties: { P0: { type: 'number' }, P1: { type: 'number' }, P2: { type: 'number' }, P3: { type: 'number' } } },
    domainBreakdown: {
      type: 'array',
      items: { type: 'object', additionalProperties: false, required: ['domain'], properties: { domain: { type: 'string' }, files: { type: 'number' }, P0: { type: 'number' }, P1: { type: 'number' }, P2: { type: 'number' }, P3: { type: 'number' } } },
    },
    top: {
      type: 'array',
      items: { type: 'object', additionalProperties: false, required: ['rank', 'severity', 'file', 'description'], properties: { rank: { type: 'number' }, severity: SEV, file: { type: 'string' }, line: { type: 'string' }, description: { type: 'string' } } },
    },
    recommendations: { type: 'array', items: { type: 'string' } },
  },
}

// ---------- resilience: retry agent() on transient failures (API 529, no-structured-output) ----------
// Identical wrapper to epic-1662-wave: the 9/9 run-#1 wipe taught us to retry at inference level.
const sleep = (ms) => new Promise((r) => { try { setTimeout(r, ms) } catch (_) { r() } })
async function tryAgent(prompt, opts, tries = 3) {
  let lastErr
  for (let attempt = 1; attempt <= tries; attempt++) {
    try { return await agent(prompt, opts) }
    catch (e) {
      lastErr = e
      if (attempt < tries) {
        log(`↻ ${opts.label || 'agent'} attempt ${attempt}/${tries} failed (${String((e && e.message) || e).slice(0, 90)}) → backoff`)
        await sleep(attempt * 20000)
      }
    }
  }
  throw lastErr
}

// ---------- prompts ----------
const auditPrompt = (d, part) => `READ-ONLY quality audit of the **${d.key}** domain, partition **${part}**, in ${REPO}.
You ANALYSE the codebase; you NEVER modify src/ or any source file. Your ONLY write is your findings report.

Domain lens: ${d.focus}
Partition: ${part} is one disjoint slice of this domain's file set. Other agents own the other slices —
scope yourself to roughly 1/${d.parts.length} of the in-scope files (deterministically, e.g. by sorted-path bucket
matching the partition index) so the domain is covered exactly once across all partitions, with no overlap.

Steps:
1. Map the in-scope files (Glob/Grep on src/factory, packages/, tests/ as the lens dictates). Select your ${part} bucket.
2. Read and audit them against the lens. For each issue: severity ∈ {P0 critical, P1 high, P2 medium, P3 low}, file, line, a precise title, why-it-matters detail, and a concrete recommendation. Be specific and skeptical — report only real, evidenced findings.
3. Write your report to ${OUT}/${d.key}/${part}.md (a markdown table of findings + a one-line scope header). Create the directory if needed. Do NOT touch anything else.
4. Return the structured result (domain, partition, reportPath, per-severity counts, findings[]).

Emitting the StructuredOutput call is mandatory; never end in prose. If a slice is clean, return findings=[] with zero counts.`

const axialPrompt = (c) => `READ-ONLY cross-domain invariant check **${c.key}** for ${REPO}. You do not modify source.
Focus: ${c.focus}
Steps:
1. Perform the check (run the named tool where applicable; otherwise inspect contracts/docs/code).
2. Write a report to ${OUT}/axial-drift/${c.key}.md enumerating contracts/invariants and their kept/broken status with the offending edge for any break.
3. Return structured: check, reportPath, kept (count), broken (count), notes. Never end in prose.`

const synthPrompt = (parts, axial) => `SYNTHESIS — aggregate the completed quality-audit into one executive summary for ${REPO}. READ-ONLY w.r.t. source.
Inputs already produced under ${OUT}/:
- ${parts} partition reports across ${DOMAINS.length} domains (${OUT}/<domain>/<Pk|Tk>.md)
- ${axial} axial-drift reports (${OUT}/axial-drift/*.md)

Steps:
1. Read every report under ${OUT}/ (Glob ${OUT}/**/*.md). Tally findings per domain and per severity (P0/P1/P2/P3).
2. Derive an overall debt score (0-100, higher = healthier; weight P0/P1 heavily) — state your weighting.
3. Write ${OUT}/AUDIT-SUMMARY.md with: executive summary (totals, debt score), a Domain Breakdown table (files · P0-P3 per domain), Top-10 critical issues (rank/severity/file:line/description), Cross-Domain Validations (axial-drift kept/broken + cocoindex), and prioritised Recommendations.
4. Return the structured summary (summaryPath, debtScore, filesAudited, totals, domainBreakdown[], top[], recommendations[]). Never end in prose.`

// ============================== ORCHESTRATION ==============================

// ---- Wave 1: AXIAL foundation (parallel — establishes the invariant baseline before domain audits) ----
phase('Axial')
log(`axial baseline: ${AXIAL_CHECKS.length} cross-domain invariant checks`)
const axial = (await parallel(AXIAL_CHECKS.map((c) => () =>
  tryAgent(axialPrompt(c), { label: `axial:${c.key}`, phase: 'Axial', schema: AXIAL_SCHEMA })))).filter(Boolean)

// ---- Waves 2-19: DOMAIN audits (read-only fan-out over domain × partition) ----
// Barrier before synthesis is the legitimate case: synthesis needs ALL partition results at once.
phase('Domains')
const units = DOMAINS.filter((d) => !ONLY || ONLY.has(d.key)).flatMap((d) => d.parts.map((p) => ({ d, p })))
log(`domain fan-out: ${units.length} partition auditors across ${DOMAINS.filter((d) => !ONLY || ONLY.has(d.key)).length} domains`)
const partResults = (await parallel(units.map((u) => () =>
  tryAgent(auditPrompt(u.d, u.p), { label: `audit:${u.d.key}:${u.p}`, phase: 'Domains', schema: FINDINGS_SCHEMA })))).filter(Boolean)

const tally = partResults.reduce((acc, r) => {
  for (const k of ['P0', 'P1', 'P2', 'P3']) acc[k] += (r.counts && r.counts[k]) || 0
  return acc
}, { P0: 0, P1: 0, P2: 0, P3: 0 })
log(`partitions done: ${partResults.length}/${units.length} · findings P0:${tally.P0} P1:${tally.P1} P2:${tally.P2} P3:${tally.P3}`)

// ---- Wave 20: SYNTHESIS (single aggregator → AUDIT-SUMMARY.md) ----
phase('Synthesis')
const summary = await tryAgent(synthPrompt(partResults.length, axial.length), { label: 'synthesis', phase: 'Synthesis', schema: SUMMARY_SCHEMA })

return {
  outDir: OUT,
  agents: axial.length + partResults.length + 1, // axial + partitions + synthesis (the 2026-06-02 run = 69)
  axial: axial.map((a) => ({ check: a.check, kept: a.kept, broken: a.broken })),
  partitions: { ran: partResults.length, planned: units.length },
  findingTotals: tally,
  summary: summary && { path: summary.summaryPath, debtScore: summary.debtScore, totals: summary.totals },
}
