export const meta = {
  name: 'multi-agent-audit',
  description: 'Run multi-agent code quality audit for lyra (waves 2-20)',
  phases: [
    { title: 'Domain Audits', detail: 'Waves 2-19 across 8 domains' },
    { title: 'Synthesis', detail: 'Generate AUDIT-SUMMARY.md' }
  ]
}

const allTasks = []

const DOMAIN_FOCUS = {
  architecture: 'Layer violations, circular deps, coupling, port/protocol boundaries, NxM duplication, cross-cutting concerns in wrong layers. Search for: import cycles, direct imports across layers, protocol violations, god modules.',
  security: 'OWASP patterns, credential handling, injection vectors, input validation, authZ gaps, secrets in code, unsafe eval/exec. Search for: password, token, secret, eval, exec, subprocess, pickle, yaml.load.',
  'code-smells': 'God classes, long functions (>80 lines), DRY violations, deep nesting, high cyclomatic complexity, dead code, duplicated logic. Search for: repeated blocks, large classes, many branches.',
  'type-safety': 'Any usage, missing type hints, type: ignore comments, untyped signatures, implicit Any. Search for: -> Any, typing.Any, # type: ignore, missing return annotations.',
  'async-patterns': 'Race conditions, blocking calls in async contexts, unawaited coroutines, resource leaks, event loop misuse. Search for: time.sleep, requests.get, httpx without async, missing await, asyncio.create_task without reference.',
  'error-handling': 'Bare excepts, swallowed errors, missing exception context, wrong exception types, no retry logic. Search for: except:, pass # ignore, except Exception as e: pass, no logging.',
  'test-quality': 'Coverage gaps, flaky patterns, mock overuse, missing assertions, test data leakage, slow tests. Search for: tests without assertions, global state mutation, monkeypatch at module level, sleep in tests.',
  'tech-debt': 'TODOs, FIXMEs, deprecated APIs, magic numbers, hardcoded values, outdated comments, unused imports. Search for: TODO, FIXME, HACK, XXX, deprecated, magic constants.'
}

const PARTS = {
  P1: { patterns: 'src/lyra/core/hub/**/*.py, src/lyra/core/lifecycle/**/*.py, src/lyra/core/ports/**/*.py', desc: 'Hub, lifecycle, ports' },
  P2: { patterns: 'src/lyra/core/agent/**/*.py, src/lyra/core/cli/**/*.py, src/lyra/core/commands/**/*.py', desc: 'Agent model, CLI, commands' },
  P3: { patterns: 'src/lyra/core/messaging/**/*.py, src/lyra/core/pool/**/*.py, src/lyra/core/stores/**/*.py, src/lyra/core/memory/**/*.py', desc: 'Messaging, pool, stores, memory' },
  P4: { patterns: 'src/lyra/core/processors/**/*.py, src/lyra/core/auth/**/*.py', desc: 'Processors, auth' },
  P5: { patterns: 'src/lyra/adapters/**/*.py, src/lyra/agent_cmd/**/*.py', desc: 'Adapters, agent_cmd' },
  P6: { patterns: 'src/lyra/bootstrap/**/*.py, src/lyra/commands/**/*.py', desc: 'Bootstrap, plugin commands' },
  P7: { patterns: 'src/lyra/infrastructure/**/*.py, src/lyra/nats/**/*.py, src/lyra/transport/**/*.py, src/lyra/obs/**/*.py', desc: 'Infra, NATS, transport, observability' },
  P8: { patterns: 'src/lyra/llm/**/*.py, src/lyra/agents/**/*.py, src/lyra/inbound/**/*.py, src/lyra/outbound/**/*.py, src/lyra/streaming/**/*.py, src/lyra/blobstore/**/*.py, src/lyra/typing/**/*.py, src/lyra/tools/**/*.py, src/lyra/monitoring/**/*.py, src/lyra/integrations/**/*.py', desc: 'LLM, agents, stages, misc' },
  T1: { patterns: 'tests/core/**/*.py', desc: 'Core unit tests' },
  T2: { patterns: 'tests/adapters/**/*.py, tests/bootstrap/**/*.py, tests/cli/**/*.py', desc: 'Adapter, bootstrap, CLI tests' },
  T3: { patterns: 'tests/infrastructure/**/*.py, tests/nats/**/*.py, tests/transport/**/*.py', desc: 'Infra, NATS, transport tests' },
  T4: { patterns: 'tests/integration/**/*.py', desc: 'Integration tests' },
  T5: { patterns: 'tests/llm/**/*.py, tests/streaming/**/*.py, tests/inbound/**/*.py, tests/outbound/**/*.py, tests/obs/**/*.py, tests/tools/**/*.py, tests/typing/**/*.py', desc: 'Stage, LLM, tools, typing tests' },
  T6: { patterns: 'tests/fakes/**/*.py, tests/factories/**/*.py, tests/helpers/**/*.py, tests/fixtures/**/*.py, tests/data/**/*.py', desc: 'Test infrastructure' }
}

const WAVE_DEFS = [
  { domain: 'architecture', parts: ['P1', 'P2', 'P3', 'P4'] },
  { domain: 'architecture', parts: ['P5', 'P6', 'P7', 'P8'] },
  { domain: 'security', parts: ['P1', 'P2', 'P3', 'P4'] },
  { domain: 'security', parts: ['P5', 'P6', 'P7', 'P8'] },
  { domain: 'code-smells', parts: ['P1', 'P2', 'P3', 'P4'] },
  { domain: 'code-smells', parts: ['P5', 'P6', 'P7', 'P8'] },
  { domain: 'code-smells', parts: ['T1', 'T2', 'T3'] },
  { domain: 'code-smells', parts: ['T4', 'T5', 'T6'] },
  { domain: 'type-safety', parts: ['P1', 'P2', 'P3', 'P4'] },
  { domain: 'type-safety', parts: ['P5', 'P6', 'P7', 'P8'] },
  { domain: 'async-patterns', parts: ['P1', 'P2', 'P3', 'P4'] },
  { domain: 'async-patterns', parts: ['P5', 'P6', 'P7', 'P8'] },
  { domain: 'error-handling', parts: ['P1', 'P2', 'P3', 'P4'] },
  { domain: 'error-handling', parts: ['P5', 'P6', 'P7', 'P8'] },
  { domain: 'test-quality', parts: ['T1', 'T2', 'T3', 'T4'] },
  { domain: 'test-quality', parts: ['T5', 'T6'] },
  { domain: 'tech-debt', parts: ['P1', 'P2', 'P3', 'P4'] },
  { domain: 'tech-debt', parts: ['P5', 'P6', 'P7', 'P8'] }
]

function buildPrompt(domain, partition) {
  const focus = DOMAIN_FOCUS[domain]
  const part = PARTS[partition]
  const outPath = 'artifacts/analyses/quality-audit/' + domain + '/' + partition + '.md'
  return 'You are a senior code quality auditor. Analyze ' + domain.toUpperCase() + ' for partition ' + partition + ' (' + part.desc + ') in the lyra project.\n\n' +
    '## Files to analyze\n' +
    'Glob patterns: ' + part.patterns + '\n' +
    'Use `Glob` and `Read` to explore these files. Focus on files that match the domain criteria.\n\n' +
    '## Focus\n' + focus + '\n\n' +
    '## Key context\n' +
    '- Lyra is an asyncio hub-spoke AI agent engine\n' +
    '- Stage-axis refactor (#1277) is in progress\n' +
    '- ADR-082 BlobStorePort introduced recently\n' +
    '- NATS transport uses JetStream, KV, and streams\n' +
    '- Production entry points: hub, adapters (telegram/discord/clipool), turn-writer\n\n' +
    '## Output\n' +
    'Write findings to: ' + outPath + '\n\n' +
    '## Format\n' +
    '### Summary (1-2 sentences)\n' +
    '### Findings\n' +
    '| severity | file | line | description |\n' +
    '|---|------|------|-------------|\n\n' +
    'P0 = critical | P1 = high | P2 = medium | P3 = low\n\n' +
    '### Metrics\n' +
    '- files_audited: N\n' +
    '- issues_found: N\n' +
    '- P0: N | P1: N | P2: N | P3: N\n\n' +
    '### Recommendations (prioritized, with effort estimate)\n\n' +
    'Rules:\n' +
    '- Cite exact file paths and line numbers\n' +
    '- If no issues found, say "No issues found" in Summary\n' +
    '- Do NOT invent findings\n' +
    '- Use `Grep` to search for patterns, then `Read` to verify\n' +
    '- If a pattern has no matches, report it as clean\n'
}

for (let i = 0; i < WAVE_DEFS.length; i++) {
  const wave = WAVE_DEFS[i]
  for (let j = 0; j < wave.parts.length; j++) {
    const part = wave.parts[j]
    allTasks.push({ domain: wave.domain, partition: part, prompt: buildPrompt(wave.domain, part) })
  }
}

log('Total audit tasks: ' + allTasks.length)

phase('Domain Audits')

const results = await pipeline(
  allTasks,
  task => agent(task.prompt, { label: task.domain + '-' + task.partition, phase: 'Domain Audits' })
)

log('Domain audits completed: ' + results.filter(Boolean).length + '/' + allTasks.length)

phase('Synthesis')

const domains = []
for (let i = 0; i < WAVE_DEFS.length; i++) {
  const d = WAVE_DEFS[i].domain
  if (domains.indexOf(d) === -1) {
    domains.push(d)
  }
}

let reportsList = ''
for (let i = 0; i < domains.length; i++) {
  reportsList += '- artifacts/analyses/quality-audit/' + domains[i] + '/*.md\n'
}

const synthPrompt = 'You are the audit synthesis lead. Read ALL domain audit reports in artifacts/analyses/quality-audit/ and produce a comprehensive AUDIT-SUMMARY.md.\n\n' +
  '## Reports to read\n' +
  reportsList +
  'Also read:\n' +
  '- artifacts/analyses/quality-audit/axial-drift/importlinter-report.md\n' +
  '- artifacts/analyses/quality-audit/axial-drift/axial-adr-review.md (if exists)\n\n' +
  '## Output\n' +
  'Write to: artifacts/analyses/quality-audit/AUDIT-SUMMARY.md\n\n' +
  '## Required sections\n' +
  '1. Executive Summary - overall health, top 3 risks, debt score (0-100)\n' +
  '2. Critical Issues (P0) - table with file, line, issue, recommended fix\n' +
  '3. High Priority (P1) - table\n' +
  '4. Medium Priority (P2) - table\n' +
  '5. Low Priority (P3) - table\n' +
  '6. Axial Drift Summary - table of wrong-axis findings\n' +
  '7. Metrics Dashboard - domain | total issues | P0 | P1 | P2 | P3\n' +
  '8. Debt Score - 0-100 (100 = clean, 0 = critical). Baseline was 72/100 (2026-04-22)\n' +
  '9. Top 10 Quick Wins - low effort, high impact fixes\n' +
  '10. Recommended Actions - prioritized roadmap with effort estimates\n\n' +
  'Rules:\n' +
  '- Dedupe findings across partitions\n' +
  '- If a report is missing or empty, note it\n' +
  '- Debt score must be justified with metrics\n' +
  '- Do NOT invent findings\n' +
  '- If reports are incomplete, say so and list what is missing\n'

const synthesis = await agent(synthPrompt, { label: 'synthesis', phase: 'Synthesis' })

return { completed: results.filter(Boolean).length, total: allTasks.length, synthesis: synthesis ? 'done' : 'failed' }
