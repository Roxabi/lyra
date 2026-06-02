# Multi-Agent Code Quality Audit — Full Plan

Target: `lyra` (staging branch) | Playbook: `roxabi-plugins/playbooks/multi-agent-audit-playbook.md` v1.1
Trigger: quarterly code-health assessment / pre-release gate

---

## 1. Scope

| Dimension | Value |
|---|---|
| Files | 416 source + 445 test = 861 Python files |
| Agents | 69 (67 playbook + 2 axial-drift gate agents) |
| Waves | 16 |
| Est. Duration | 65–75 min |
| Debt Score Baseline | 72/100 (2026-04-22) |

---

## 2. Partitioning

### Source (P1–P8)

| ID | Patterns | Description |
|---|---|---|
| P1 | `core/hub/**/*.py`, `core/lifecycle/**/*.py`, `core/ports/**/*.py` | Hub router, lifecycle, ports |
| P2 | `core/agent/**/*.py`, `core/cli/**/*.py`, `core/commands/**/*.py` | Agent model, CLI, command registry |
| P3 | `core/messaging/**/*.py`, `core/pool/**/*.py`, `core/stores/**/*.py`, `core/memory/**/*.py` | Messaging, pool, stores, memory |
| P4 | `core/processors/**/*.py`, `core/auth/**/*.py` | Processors, auth |
| P5 | `adapters/**/*.py`, `agent_cmd/**/*.py` | Platform adapters + agent_cmd layer |
| P6 | `bootstrap/**/*.py`, `commands/**/*.py` | Bootstrap/wiring + plugin commands |
| P7 | `infrastructure/**/*.py`, `nats/**/*.py`, `transport/**/*.py`, `obs/**/*.py` | Infra, NATS, transport, observability |
| P8 | `llm/**/*.py`, `agents/**/*.py`, `inbound/**/*.py`, `outbound/**/*.py`, `streaming/**/*.py`, `blobstore/**/*.py`, `typing/**/*.py`, `tools/**/*.py`, `monitoring/**/*.py`, `integrations/**/*.py` | LLM, agents, stages, misc |

### Test (T1–T6)

| ID | Patterns | Description |
|---|---|---|
| T1 | `tests/core/**/*.py` | Core unit tests |
| T2 | `tests/adapters/**/*.py`, `tests/bootstrap/**/*.py`, `tests/cli/**/*.py` | Adapter + bootstrap + CLI tests |
| T3 | `tests/infrastructure/**/*.py`, `tests/nats/**/*.py`, `tests/transport/**/*.py` | Infra + NATS + transport tests |
| T4 | `tests/integration/**/*.py` | Integration tests |
| T5 | `tests/llm/**/*.py`, `tests/streaming/**/*.py`, `tests/inbound/**/*.py`, `tests/outbound/**/*.py`, `tests/obs/**/*.py`, `tests/tools/**/*.py`, `tests/typing/**/*.py` | Stage + LLM + tools + typing tests |
| T6 | `tests/fakes/**/*.py`, `tests/factories/**/*.py`, `tests/helpers/**/*.py`, `tests/fixtures/**/*.py`, `tests/data/**/*.py` | Test infrastructure & coverage |

---

## 3. Domains

| # | Domain | Key Metrics |
|---|---|---|
| 1 | Architecture | Layer violations, circular deps, coupling |
| 2 | Axial Drift | Wrong-axis duplication (N×M), cross-cutting concerns |
| 3 | Security | OWASP, credentials, injection vectors |
| 4 | Code Smells | God classes, long functions, DRY |
| 5 | Type Safety | `Any` usage, missing hints, `type: ignore` |
| 6 | Async Patterns | Race conditions, blocking calls, leaks |
| 7 | Error Handling | Bare excepts, swallowed errors, missing context |
| 8 | Test Quality | Coverage, flaky patterns, mock usage |
| 9 | Tech Debt | TODOs, FIXMEs, deprecated APIs, magic numbers |

---

## 4. Waves

| Wave | Agents | Domain | Partitions |
|---|---|---|---|
| 1 | 2 | Axial Drift | importlinter, axial-adr-review |
| 2 | 4 | Architecture | P1–P4 |
| 3 | 4 | Architecture | P5–P8 |
| 4 | 4 | Security | P1–P4 |
| 5 | 4 | Security | P5–P8 |
| 6 | 4 | Code Smells | P1–P4 |
| 7 | 4 | Code Smells | P5–P8 |
| 8 | 3 | Code Smells | T1–T3 |
| 9 | 3 | Code Smells | T4–T6 |
| 10 | 4 | Type Safety | P1–P4 |
| 11 | 4 | Type Safety | P5–P8 |
| 12 | 4 | Async Patterns | P1–P4 |
| 13 | 4 | Async Patterns | P5–P8 |
| 14 | 4 | Error Handling | P1–P4 |
| 15 | 4 | Error Handling | P5–P8 |
| 16 | 4 | Test Quality | T1–T4 |
| 17 | 2 | Test Quality | T5–T6 |
| 18 | 4 | Tech Debt | P1–P4 |
| 19 | 4 | Tech Debt | P5–P8 |
| 20 | 1 | Synthesis | AUDIT-SUMMARY.md |

---

## 5. Axial Drift Gate

### Step 1: Structural Check
```bash
uv run import-linter
# or
importlinter
```
Output: `artifacts/analyses/quality-audit/axial-drift/importlinter-report.md`

### Step 2: Semantic Check
Spawn `dev-core:axial-adr-review` against diff since last audit baseline (`ed2c394`).
Tag: `target-axis-trap`

### Step 3: Cocoindex Confirmation
```bash
# Retry logic duplication across stores
ccc search "retry logic" --path "infrastructure/stores/*" --limit 20
# Auth middleware pattern in adapters
ccc search "auth middleware" --path "adapters/*" --limit 20
# wait_for_hub pattern
ccc search "def wait_for_hub" --limit 20
```

---

## 6. Agent Prompt Template

```
## Task
Analyze {DOMAIN} for partition {PARTITION}.

## Files
{PATTERNS}

## Focus
- {domain-specific bullets}

## Output
Write to: artifacts/analyses/quality-audit/{DOMAIN}/{PARTITION}.md

## Format
### Summary
### Findings (severity | file | line | description)
### Metrics
### Recommendations
```

---

## 7. Output Structure

```
artifacts/analyses/quality-audit/
├── STRATEGY.md
├── AGENT_PROMPTS.md
├── manifest.json
├── AUDIT-SUMMARY.md
├── axial-drift/
│   ├── importlinter-report.md
│   ├── axial-adr-review.md
│   └── cocoindex-confirmations.md
├── architecture/
│   ├── P01.md … P08.md
├── security/
│   ├── P01.md … P08.md
├── code-smells/
│   ├── P01.md … P08.md
│   └── T01.md … T06.md
├── type-safety/
│   ├── P01.md … P08.md
├── async-patterns/
│   ├── P01.md … P08.md
├── error-handling/
│   ├── P01.md … P08.md
├── test-quality/
│   └── T01.md … T06.md
└── tech-debt/
    ├── P01.md … P08.md
```

---

## 8. Manifest

```json
{
  "status": "pending",
  "started": null,
  "completed_agents": [],
  "pending_agents": [],
  "current_wave": 0,
  "waves": {
    "1": ["axial-importlinter", "axial-adr-review"],
    "2": ["arch-P1", "arch-P2", "arch-P3", "arch-P4"],
    "3": ["arch-P5", "arch-P6", "arch-P7", "arch-P8"],
    "4": ["sec-P1", "sec-P2", "sec-P3", "sec-P4"],
    "5": ["sec-P5", "sec-P6", "sec-P7", "sec-P8"],
    "6": ["smell-P1", "smell-P2", "smell-P3", "smell-P4"],
    "7": ["smell-P5", "smell-P6", "smell-P7", "smell-P8"],
    "8": ["smell-T1", "smell-T2", "smell-T3"],
    "9": ["smell-T4", "smell-T5", "smell-T6"],
    "10": ["type-P1", "type-P2", "type-P3", "type-P4"],
    "11": ["type-P5", "type-P6", "type-P7", "type-P8"],
    "12": ["async-P1", "async-P2", "async-P3", "async-P4"],
    "13": ["async-P5", "async-P6", "async-P7", "async-P8"],
    "14": ["err-P1", "err-P2", "err-P3", "err-P4"],
    "15": ["err-P5", "err-P6", "err-P7", "err-P8"],
    "16": ["test-T1", "test-T2", "test-T3", "test-T4"],
    "17": ["test-T5", "test-T6"],
    "18": ["debt-P1", "debt-P2", "debt-P3", "debt-P4"],
    "19": ["debt-P5", "debt-P6", "debt-P7", "debt-P8"],
    "20": ["synthesis"]
  }
}
```

---

## 9. Quick-Start

```bash
# 1. Init directories
mkdir -p artifacts/analyses/quality-audit/{axial-drift,architecture,security,code-smells,type-safety,async-patterns,error-handling,test-quality,tech-debt}

# 2. Seed manifest
cat > artifacts/analyses/quality-audit/manifest.json << 'EOF'
{"status":"in_progress","started":"$(date -u +%Y-%m-%dT%H:%M:%SZ)","completed_agents":[],"pending_agents":[],"current_wave":0,"waves":{}}
EOF

# 3. Run Wave 1 (structural gate)
uv run import-linter > artifacts/analyses/quality-audit/axial-drift/importlinter-report.md 2>&1

# 4. Run Wave 2–20 via Workflow / parallel agents
#    (see playbook §Execution Pattern)
```

---

## 10. Cocoindex Cross-Domain Validation

Batch confirmation queries per domain:

| Domain | Queries |
|---|---|
| Security | `ccc search "password" --limit 20`, `ccc search "API key" --limit 20`, `ccc search "token" --path "config/*" --limit 20` |
| Code Smells | `ccc search "def extract_json" --limit 20`, `ccc search "parse_timestamp" --limit 20` |
| Type Safety | `ccc search "def process_" --lang python --limit 50` |
| Async | `ccc search "requests.get" --path "adapters/*" --limit 20`, `ccc search "time.sleep" --path "src/*" --limit 20` |
| Error Handling | `ccc search "except:" --lang python --limit 30`, `ccc search "pass  # ignore" --lang python --limit 20` |
| Tech Debt | `ccc search "TODO:" --limit 50`, `ccc search "FIXME" --limit 50`, `ccc search "HACK" --limit 50` |

---

## 11. Synthesis Deliverable

`AUDIT-SUMMARY.md` must contain:
- Executive Summary
- Critical Issues (P0)
- High Priority (P1)
- Medium Priority (P2)
- Low Priority (P3)
- Axial Drift Summary table
- Metrics Dashboard (domain | issues | P0 | P1 | P2 | P3)
- Recommended Actions (prioritized + effort)
- Technical Debt Score (0–100)
- Top 10 Quick Wins

---

## 12. Known Baselines from Prior Audit (2026-04-22)

| Metric | Prior Value |
|---|---|
| Total Issues | 160 |
| P0 | 6 |
| P1 | 19 |
| P2 | 66 |
| P3 | 69 |
| Axial Drift Violations | 3 |
| N×M Traps | 2 |
| Cocoindex Confirmations | 8 |
| Debt Score | 72/100 |

> Focus delta: stage-axis refactor (#1277) in progress since 2026-05-09 — expect new axial drift in `infrastructure/stores/nats.py` and `infrastructure/stores/redis.py` if parallel migration paths exist.
