// Workflow: epic-1662-wave — run ONE dependency-wave of epic #1662 leaves, fully autonomous.
//
// Per issue (adaptive to size):
//   S            : single impl agent → branch feat/N → gates → PR
//   F-lite/Ffull : PLAN (decompose into disjoint file-groups) → SUB-IMPL N× (parallel, sub-branches)
//                  → INTEGRATE (merge disjoint sub-branches → full gates → PR)
//   then ∀ size  : [REVIEW panel (5 lenses) → VALIDATE (2 skeptics/finding) → FIX (fixer)] × ≤2 rounds
//                  → DEFER residual as sibling issue (parent = parent(N), blocked-by N)
// Does NOT merge. Main loop runs: CI-green check → +reviewed label → gh pr merge --auto → /ci-watch,
// then re-invokes this workflow for the next wave (dependents branch from updated, merged staging).
//
// Invoke:  Workflow({ scriptPath: ".../epic-1662-wave.mjs", args: [1636,1637,1660] })

export const meta = {
  name: 'epic-1662-wave',
  description: 'Autonomous per-wave dev of epic #1662 leaves: size-adaptive decomposed impl + 5-lens cross-agent review/validate/fix loop + defer residual; merge handled by main loop',
  phases: [
    { title: 'Plan', detail: 'decompose F-lite/F-full into disjoint file-groups' },
    { title: 'Implement', detail: 'sub-impl per file-group → integrate → PR with decision trace' },
    { title: 'Review', detail: '5-lens panel: correctness · axial · security · tests · rules' },
    { title: 'Validate', detail: '2 skeptics/finding, refute-by-default' },
    { title: 'Fix', detail: 'apply confirmed findings; defer residual after 2 rounds' },
  ],
}

const REPO = 'Roxabi/roxabi-factory'
const BASE = 'staging'
const CAP = 3 // max issues in flight per batch (merge-cadence; engine also caps total agents at min(16,cores-2))
const TRIAGE = 'bun /home/mickael/.claude/plugins/cache/roxabi-marketplace/dev-core/0.5.0/skills/issue-triage/triage.ts'

const RULES = `RÈGLES (∀ P, verbatim) :
P:=problème · obs(P),Sym(P):=observable,symptômes · RC(P):=root cause(s)
Corr(P):={corrections} · Patch⊂Corr(local) · Archi⊂Corr(structurel) · Patch∩Archi=∅ · I:=input · trust(I):=confiance · C:=code · L:=niveau logique
1. RCA      : dériver RC(P). ¬fix sur obs/Sym. symptôme ≠ cause.
2. Corr     : énumérer; classer chaque ∈ Patch ⊻ Archi; CHOISIR explicitement (¬patch déguisé en archi).
3. fix@L    : appliquer F au niveau L = niveau(RC), ¬ au niveau où Sym apparaît.
4. trust(I) : ∀ I évaluer trust(I) AVANT usage; trust bas → valider/sanitize.
5. ✓        : tested(C) ∧ correct(C). vert ≠ correct. exiger les deux.
6. Doc(PR)  : tracer RC(P) + Corr choisie + classe(Patch|Archi) + niveau L.`

const AXIAL = 'Axial mandate (ADR-073): primary axis = STAGE. inbound/ must NOT import from adapters/ (non-primary axis). Stage primitives live in core/ or a floating shared module. Flag any inbound→adapters import, cross-layer leak, or N×M duplication across non-primary siblings.'

const SIZE = {
  1636: 'S', 1637: 'F-lite', 1639: 'S', 1659: 'F-lite', 1660: 'F-lite', 1661: 'F-lite',
  1663: 'F-lite', 1664: 'S', 1665: 'S', 1635: 'F-lite', 1666: 'F-lite', 1667: 'S',
  1652: 'S', 1653: 'S', 1654: 'S', 1655: 'S', 1656: 'S', 1657: 'S', 1658: 'S',
}
// 1639 bumped S→F-lite after run#2 (single heavy agent ended in prose; decompose into lighter slices).
SIZE[1639] = 'F-lite'

const FOOTPRINT = {
  1636: 'core/pool/pool_processor_exec.py — split 165-line god method into ≤40-line helpers',
  1637: 'infrastructure/stores/turn_store.py + turn_writer — Result/NACK/DLQ, no silent turn loss',
  1639: 'bootstrap/{standalone,infra,factory}/*, commands/svc/handlers.py — narrow except Exception',
  1659: 'core/config/{bus,memory,platform,turn_store}_config.py — NEW config dataclasses',
  1660: 'adapters/shared/{base_platform_adapter,base_formatter,platform_send}.py + tg/dc formatters+outbound',
  1661: 'core/ports/{llm,llm_types}.py, core/stores/{auth,identity_alias}_store_protocol.py, tts_protocol.py, authenticator, hub/outbound, .importlinter',
  1663: 'bootstrap/wiring/{standalone_telegram,standalone_discord,bootstrap_wiring,nats_wiring}.py — parameterize',
  1664: 'bootstrap/factory/voice_overlay.py — extract init_nats_worker(service)',
  1665: 'agent_cmd/platforms/{telegram,discord}.py → single parameterized platform.py',
  1635: 'core call-sites (lifecycle, messaging, memory, hub/outbound, agent) → wire to #1659 configs',
  1666: 'inbound/{dispatcher,context}.py — relocate push_to_hub_guarded/PushGuardDeps/OutboundListener/TypingTaskManager out of adapters.shared',
  1667: 'inbound/wire_parser_{telegram,discord}.py + Parser Protocol feed/finalize/is_done aliases + drop .importlinter exemptions',
  1652: 'tools/check_debt_expiry.sh + .pre-commit-config.yaml + ci.yml',
  1653: 'tools/check_test_sleep.sh + .pre-commit-config.yaml + ci.yml',
  1654: 'tools/check_hardcoded_constants.sh + .pre-commit-config.yaml + ci.yml',
  1655: 'tools/file_exemptions.txt + tools/check_file_exemptions.sh + ci.yml',
  1656: '.github/pull_request_template.md — debt/sleep/constant checklist',
  1657: '.github/workflows/axial-review.yml + CLAUDE.md',
  1658: 'CLAUDE.md / docs/process/dev-cycle.md — debt retrospective',
}

const LENSES = [
  { key: 'correctness', agent: 'dev-core:backend-dev', focus: 'logic bugs, regressions, behavioural drift on pure refactors, edge cases' },
  { key: 'axial', agent: 'dev-core:axial-adr-review', focus: AXIAL },
  { key: 'security', agent: 'dev-core:security-auditor', focus: 'OWASP, untrusted input handling, broad-catch masking, secret/credential exposure' },
  { key: 'tests', agent: 'dev-core:tester', focus: 'do tests prove the behaviour (not just pass)? coverage of acceptance criteria? no sleep()-based flakiness' },
  { key: 'rules', agent: 'dev-core:backend-dev', focus: 'RULES compliance: fix@root-cause-level L not symptom; honest Patch⊻Archi classification; no patch disguised as archi' },
]

let _raw = args
if (typeof _raw === 'string') { try { _raw = JSON.parse(_raw) } catch (e) { /* leave as string */ } }
const wave = Array.isArray(_raw)
  ? _raw.map(Number).filter((x) => Number.isFinite(x))
  : (_raw && Array.isArray(_raw.issues) ? _raw.issues.map(Number).filter((x) => Number.isFinite(x)) : [])
if (!wave.length) throw new Error(`args resolved empty. typeof args=${typeof args}; value=${JSON.stringify(args)?.slice(0, 200)}. Expected array of issue numbers, e.g. [1636,1637,1660]`)

// ---------- schemas ----------
const PLAN_SCHEMA = {
  type: 'object', additionalProperties: false, required: ['tasks'],
  properties: {
    cohesive: { type: 'boolean' },
    tasks: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false, required: ['id', 'title', 'fileGroup', 'detail'],
        properties: {
          id: { type: 'string' }, title: { type: 'string' },
          fileGroup: { type: 'array', items: { type: 'string' } }, detail: { type: 'string' },
        },
      },
    },
  },
}
const SUBIMPL_SCHEMA = {
  type: 'object', additionalProperties: false, required: ['taskId', 'branch', 'status'],
  properties: {
    taskId: { type: 'string' }, branch: { type: 'string' },
    status: { type: 'string', enum: ['pushed', 'failed'] },
    files: { type: 'array', items: { type: 'string' } }, notes: { type: 'string' },
  },
}
const IMPL_SCHEMA = {
  type: 'object', additionalProperties: false, required: ['issue', 'status', 'branch', 'prUrl', 'classification', 'rootCause'],
  properties: {
    issue: { type: 'number' }, status: { type: 'string', enum: ['pr-opened', 'failed', 'partial'] },
    branch: { type: 'string' }, prUrl: { type: 'string' },
    classification: { type: 'string', enum: ['Patch', 'Archi', 'Mixed'] },
    rootCause: { type: 'string' }, filesChanged: { type: 'array', items: { type: 'string' } },
    gates: { type: 'object', properties: { pytest: { type: 'boolean' }, ruff: { type: 'boolean' }, pyright: { type: 'boolean' }, importlinter: { type: 'boolean' } } },
    diff: { type: 'string' }, notes: { type: 'string' },
  },
}
const FINDINGS_SCHEMA = {
  type: 'object', additionalProperties: false, required: ['lens', 'findings'],
  properties: {
    lens: { type: 'string' },
    findings: {
      type: 'array',
      items: {
        type: 'object', additionalProperties: false, required: ['id', 'severity', 'title', 'detail'],
        properties: {
          id: { type: 'string' }, severity: { type: 'string', enum: ['blocking', 'major', 'minor', 'nit'] },
          file: { type: 'string' }, line: { type: 'string' }, title: { type: 'string' },
          detail: { type: 'string' }, suggestion: { type: 'string' },
        },
      },
    },
  },
}
const VERDICT_SCHEMA = {
  type: 'object', additionalProperties: false, required: ['real', 'reason'],
  properties: { real: { type: 'boolean' }, reason: { type: 'string' } },
}
const FIX_SCHEMA = {
  type: 'object', additionalProperties: false, required: ['round', 'fixed', 'residual', 'diff'],
  properties: {
    round: { type: 'number' },
    fixed: { type: 'array', items: { type: 'string' } },
    residual: { type: 'array', items: { type: 'object', additionalProperties: false, required: ['title', 'detail'], properties: { id: { type: 'string' }, title: { type: 'string' }, detail: { type: 'string' } } } },
    diff: { type: 'string' }, gatesPass: { type: 'boolean' },
  },
}
const DEFER_SCHEMA = {
  type: 'object', additionalProperties: false, required: ['issueUrl'],
  properties: { issueUrl: { type: 'string' }, parent: { type: 'number' } },
}
const DIFF_SCHEMA = {
  type: 'object', additionalProperties: false, required: ['diff'],
  properties: { diff: { type: 'string' } },
}

// ---------- prompts ----------
const planPrompt = (n) => `Decompose GitHub issue #${n} (${REPO}) into INDEPENDENT implementation tasks with DISJOINT file groups, so each can be implemented by a separate agent without merge conflicts.
Read: gh issue view ${n} --json title,body. Footprint hint: ${FOOTPRINT[n] || '(read the issue)'}.
Explore the repo (Read/Grep) enough to map files. Rules:
- Each task.fileGroup must be a set of files NO other task touches (strict disjointness — this guarantees conflict-free integration).
- If the issue is cohesive (one tight unit, or shared file that can't be split), return a SINGLE task with cohesive=true.
- Prefer 2-4 tasks for F-lite, 3-6 for F-full. Never split so fine that tasks share files.
Return ONLY the structured plan.`

const subImplPrompt = (n, t) => `You are ONE of several agents implementing GitHub issue #${n} (${REPO}) in parallel. Your slice ONLY:
TASK ${t.id}: ${t.title}
FILE GROUP (touch ONLY these — another agent owns the rest): ${JSON.stringify(t.fileGroup)}
DETAIL: ${t.detail}

You are in a fresh isolated worktree off origin/${BASE}. Steps:
1. git checkout -B wf/${n}/${t.id} origin/${BASE}   (-B resets the local branch if a prior attempt left one)
2. Implement ONLY your file group per the issue's acceptance + RULES. Do NOT edit files outside your group.
3. Format your files: uv run ruff format <your files> ; uv run ruff check --fix <your files>. (Full test suite runs at integration — but do not leave obvious breakage.)
4. Commit (Conventional Commits; NEVER --amend/--force/--hard) and: git push -u origin wf/${n}/${t.id}
5. STOP. Return the structured result (status='pushed').

${RULES}`

const implWholePrompt = (n) => `Implement GitHub issue #${n} (${REPO}) end-to-end. You are in a fresh isolated worktree off origin/${BASE}. Footprint: ${FOOTPRINT[n] || '(read issue)'}.
1. gh issue view ${n} --json title,body — acceptance criteria = contract.
2. git checkout -B feat/${n}-impl origin/${BASE}   (-B resets the local branch if a prior attempt left one)
3. Implement per RULES (pure refactors keep behaviour identical).
4. GATES all green: uv run ruff format . && uv run ruff check --fix . ; uv run pytest ; uv run pyright ; if .importlinter touched, uv run lint-imports. Then ALWAYS run python tools/generate_architecture_snapshot.py . and COMMIT docs/architecture/CURRENT.generated.md if it changed (the CI architecture_snapshot gate rejects a stale snapshot). If you modify ANY .github/workflows/*.yml: validate each parses with python -c "import yaml,sys; yaml.safe_load(open(sys.argv[1]))" <file> AND confirm the required job named "ci" still exists — QUOTE any step name/value containing a colon (e.g. name: "... DEBT: ..."), an unquoted colon makes the whole workflow invalid and silently drops the ci check. Respect 300-line/file gate.
5. Commit, push, open PR (gh pr create --base ${BASE}). PR body MUST include "## Decision trace": RC(P) · candidate Corr · chosen Corr + classe Patch|Archi|Mixed (justify) · logic level L. Add "Closes #${n}".
6. Return the structured result with status='pr-opened', branch, prUrl, classification, rootCause. diff is OPTIONAL: include (gh pr diff <branch>) truncated to ~10000 chars ONLY if your response stays short; if the work is large, set diff='' and STILL emit the StructuredOutput call — a separate step fetches the diff. Emitting StructuredOutput is mandatory; never end in prose.

${RULES}`

const integratePrompt = (n, subBranches) => `Integrate the parallel sub-implementations of issue #${n} (${REPO}) into one PR. Sub-branches (disjoint file groups, already pushed): ${JSON.stringify(subBranches)}.
You are in a fresh isolated worktree. Steps:
1. git fetch origin
2. git checkout -B feat/${n} origin/${BASE}   (-B resets the local branch if a prior attempt left one)
3. Merge each sub-branch (disjoint files → must be conflict-free): for B in sub-branches: git merge --no-edit origin/B. If an unexpected conflict appears, resolve minimally and note it.
4. FULL GATES green: uv run ruff format . && uv run ruff check --fix . ; uv run pytest ; uv run pyright ; if .importlinter touched, uv run lint-imports. Then ALWAYS run python tools/generate_architecture_snapshot.py . and COMMIT docs/architecture/CURRENT.generated.md if it changed (CI architecture_snapshot gate rejects a stale snapshot). Fix integration seams (imports, wiring) until green. Respect 300-line/file gate.
5. git push -u origin feat/${n}. Open PR: gh pr create --base ${BASE}. PR body MUST include "## Decision trace": RC(P) · candidate Corr · chosen Corr + classe Patch|Archi|Mixed (justify) · logic level L. Add "Closes #${n}".
6. Return structured result: status='pr-opened', branch='feat/${n}', prUrl, classification, rootCause. diff is OPTIONAL — set diff='' if the work is large; ALWAYS emit the StructuredOutput call, never end in prose.

${RULES}`

const reviewPrompt = (n, L, diff) => `Review the PR diff for issue #${n} (${REPO}) through the **${L.key}** lens ONLY.
Lens focus: ${L.focus}
Acceptance contract: run/recall gh issue view ${n}. Be specific and skeptical; report only real, actionable findings (file + line + why + concrete suggestion). Severity ∈ {blocking,major,minor,nit}.

----- DIFF (truncated) -----
${(diff || '').slice(0, 12000)}
----------------------------

${RULES}
Return ONLY structured findings (empty array if clean).`

const validatePrompt = (n, f, diff, k) => `SKEPTIC #${k}. A reviewer claims this finding on issue #${n}'s PR. Your job is to REFUTE it. Default real=false unless the evidence in the diff is incontrovertible.
FINDING [${f.severity}] ${f.title}
${f.detail}${f.file ? '\nat ' + f.file + (f.line ? ':' + f.line : '') : ''}

----- DIFF (truncated) -----
${(diff || '').slice(0, 9000)}
----------------------------
Is it a REAL, must-fix problem (not noise/style/false-positive)? Return ONLY the verdict.`

const fixPrompt = (n, branch, confirmed, round) => `Apply confirmed review findings to PR branch ${branch} of issue #${n} (${REPO}), round ${round}/2. You are in a fresh isolated worktree.
1. git fetch origin && git checkout ${branch} && git reset --hard origin/${branch}
2. Apply ONLY these confirmed findings (do not refactor beyond them):
${confirmed.map((f, i) => `  ${i + 1}. [${f.severity}] ${f.title} — ${f.detail}${f.file ? ' @' + f.file : ''}`).join('\n')}
3. GATES green: uv run ruff format . && uv run ruff check --fix . ; uv run pytest ; uv run pyright ; if .importlinter touched, uv run lint-imports. Then run python tools/generate_architecture_snapshot.py . and COMMIT docs/architecture/CURRENT.generated.md if it changed (CI architecture_snapshot gate).
4. Commit (Conventional; NEVER --amend/--force) and git push origin ${branch}.
5. Return: which finding ids you 'fixed', which remain 'residual' (could not safely fix), and diff = (gh pr diff ${branch}) truncated ~12000 chars.

${RULES}`

const diffPrompt = (n, branch) => `Output ONLY the unified diff of PR branch ${branch} (issue #${n}, ${REPO}). Run: gh pr diff ${branch}. Return it verbatim in the 'diff' field, truncated to ~10000 chars. Do nothing else — no analysis, no edits.`

const deferPrompt = (n, residual) => `Create a DEFERRED follow-up issue for unresolved review findings on issue #${n} (${REPO}), as a SIBLING (shared parent), blocked-by #${n}.
1. Resolve parent: P=$(gh api graphql -f query='query{repository(owner:"Roxabi",name:"roxabi-factory"){issue(number:${n}){parent{number}}}}' --jq '.data.repository.issue.parent.number // empty')
2. Create via triage CLI (sibling rule):
   ${TRIAGE} create --title "review residual: from #${n}" --body "**Origin:** #${n} (deferred after 2 fix rounds)\\n\\n${residual.map((r) => '- ' + r.title + ': ' + r.detail).join('\\n')}" --blocked-by "#${n}" ${'${P:+--parent "#$P"}'}
3. Return the new issue URL (and parent number if any).`

// ---------- resilience: retry agent() on transient failures (API 529 Overloaded, no-structured-output) ----------
// RC of the first launch: every agent's first inference returned 529 → never called StructuredOutput → throw.
// Retry at the inference level with guarded backoff. Backoff sleeps only if setTimeout exists (sandbox-safe).
const sleep = (ms) => new Promise((r) => { try { setTimeout(r, ms) } catch (_) { r() } })
async function tryAgent(prompt, opts, tries = 3) {
  let lastErr
  for (let attempt = 1; attempt <= tries; attempt++) {
    try {
      return await agent(prompt, opts)
    } catch (e) {
      lastErr = e
      if (attempt < tries) {
        log(`↻ ${opts.label || 'agent'} attempt ${attempt}/${tries} failed (${String((e && e.message) || e).slice(0, 90)}) → backoff`)
        await sleep(attempt * 20000)
      }
    }
  }
  throw lastErr
}

// ---------- per-issue pipeline ----------
async function devIssue(n) {
  const size = SIZE[n] || 'S'
  // 1. PLAN / 2. SUB-IMPL / 3. INTEGRATE → impl (has prUrl + diff)
  let impl
  if (size === 'S') {
    impl = await tryAgent(implWholePrompt(n), { label: `impl:${n}`, phase: 'Implement', isolation: 'worktree', agentType: 'dev-core:backend-dev', schema: IMPL_SCHEMA })
  } else {
    const plan = await tryAgent(planPrompt(n), { label: `plan:${n}`, phase: 'Plan', agentType: 'dev-core:backend-dev', schema: PLAN_SCHEMA })
    const tasks = plan && plan.tasks && plan.tasks.length ? plan.tasks : null
    if (!tasks || tasks.length <= 1) {
      impl = await tryAgent(implWholePrompt(n), { label: `impl:${n}`, phase: 'Implement', isolation: 'worktree', agentType: 'dev-core:backend-dev', schema: IMPL_SCHEMA })
    } else {
      log(`#${n}: ${tasks.length} disjoint tasks → parallel sub-impl`)
      const subs = await parallel(tasks.map((t) => () =>
        tryAgent(subImplPrompt(n, t), { label: `impl:${n}:${t.id}`, phase: 'Implement', isolation: 'worktree', agentType: 'dev-core:backend-dev', schema: SUBIMPL_SCHEMA })))
      const subBranches = subs.filter(Boolean).filter((s) => s.status === 'pushed').map((s) => s.branch)
      if (!subBranches.length) return { issue: n, status: 'failed', stage: 'sub-impl' }
      impl = await tryAgent(integratePrompt(n, subBranches), { label: `integrate:${n}`, phase: 'Implement', isolation: 'worktree', agentType: 'dev-core:backend-dev', schema: IMPL_SCHEMA })
    }
  }
  if (!impl || impl.status !== 'pr-opened') return { issue: n, status: 'failed', stage: 'impl', impl }

  // diff fetch fallback: impl may omit diff (large work) → fetch out-of-band via a cheap agent
  let diff = impl.diff
  if (!diff || diff.length < 40) {
    const df = await tryAgent(diffPrompt(n, impl.branch), { label: `diff:${n}`, phase: 'Implement', agentType: 'dev-core:backend-dev', schema: DIFF_SCHEMA })
    diff = (df && df.diff) || ''
  }

  // 4-6. REVIEW → VALIDATE → FIX, ≤2 rounds
  let residual = []
  for (let round = 1; round <= 2; round++) {
    const reviews = await parallel(LENSES.map((L) => () =>
      tryAgent(reviewPrompt(n, L, diff), { label: `review:${n}:${L.key}:r${round}`, phase: 'Review', agentType: L.agent, schema: FINDINGS_SCHEMA })))
    const findings = reviews.filter(Boolean).flatMap((r) => (r.findings || []).map((f, i) => ({ ...f, id: `${r.lens}-${f.id || i}` })))
      .filter((f) => f.severity === 'blocking' || f.severity === 'major')
    if (!findings.length) { residual = []; break }

    const verdicts = await parallel(findings.map((f) => () =>
      parallel([1, 2].map((k) => () => tryAgent(validatePrompt(n, f, diff, k), { label: `validate:${n}:${f.id}:${k}`, phase: 'Validate', schema: VERDICT_SCHEMA })))
        .then((votes) => ({ f, real: votes.filter(Boolean).some((v) => v.real) }))))
    const confirmed = verdicts.filter((v) => v.real).map((v) => v.f)
    if (!confirmed.length) { residual = []; break }

    log(`#${n} round ${round}: ${confirmed.length} confirmed finding(s) → fix`)
    const fix = await tryAgent(fixPrompt(n, impl.branch, confirmed, round), { label: `fix:${n}:r${round}`, phase: 'Fix', isolation: 'worktree', agentType: 'dev-core:fixer', schema: FIX_SCHEMA })
    diff = (fix && fix.diff) || diff
    residual = (fix && fix.residual) || confirmed.map((f) => ({ title: f.title, detail: f.detail }))
    if (!residual.length) break
  }

  // 7. DEFER residual
  let deferred = null
  if (residual.length) {
    deferred = await tryAgent(deferPrompt(n, residual), { label: `defer:${n}`, phase: 'Fix', agentType: 'dev-core:backend-dev', schema: DEFER_SCHEMA })
  }
  return { issue: n, status: 'pr-opened', prUrl: impl.prUrl, classification: impl.classification, rootCause: impl.rootCause, gates: impl.gates, residual, deferred }
}

// ---------- failure policy: ISOLATE-DEFER-CONTINUE ----------
// Any thrown error OR status:'failed' inside devIssue is caught here, logged, and recorded as a
// failed result — never null-dropped, never aborts the batch/wave. The main loop reads `failed[]`
// to isolate the issue (leave its branch/PR as-is), defer it (skip its dependents in later waves),
// and continue. Other issues in the same batch are unaffected.
async function runIssue(n) {
  try {
    const r = await devIssue(n)
    if (!r || r.status === 'failed') {
      log(`⛔ #${n} FAILED at stage=${(r && r.stage) || 'unknown'} → isolated, deferred, wave continues`)
      return { issue: n, status: 'failed', stage: (r && r.stage) || 'unknown' }
    }
    return r
  } catch (e) {
    log(`⛔ #${n} THREW (${String(e && e.message || e).slice(0, 200)}) → isolated, deferred, wave continues`)
    return { issue: n, status: 'failed', stage: 'exception', error: String(e && e.message || e).slice(0, 500) }
  }
}

// ---------- wave driver (≤CAP issues per batch for merge cadence) ----------
const out = []
for (let i = 0; i < wave.length; i += CAP) {
  const batch = wave.slice(i, i + CAP)
  log(`batch → #${batch.join(' #')}`)
  const res = await parallel(batch.map((n) => () => runIssue(n)))
  out.push(...res.filter(Boolean))
}

const failed = out.filter((r) => r.status === 'failed').map((r) => ({ issue: r.issue, stage: r.stage, error: r.error }))
const shipped = out.filter((r) => r.status === 'pr-opened')
if (failed.length) log(`wave done: ${shipped.length} PR(s) opened, ${failed.length} isolated/deferred → #${failed.map((f) => f.issue).join(' #')}`)
else log(`wave done: ${shipped.length} PR(s) opened, 0 failures`)

return {
  wave,
  failed, // main loop: skip these issues' dependents in subsequent waves
  results: out.map((r) => ({
    issue: r.issue, status: r.status, stage: r.stage, prUrl: r.prUrl, classification: r.classification,
    residualCount: (r.residual || []).length, deferredUrl: r.deferred && r.deferred.issueUrl,
  })),
}
