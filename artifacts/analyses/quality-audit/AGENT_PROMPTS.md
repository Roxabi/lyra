# Agent Prompt Definitions

## Partition File Patterns

| ID | Patterns |
|----|----------|
| P1 | `src/lyra/core/hub/**/*.py` |
| P2 | `src/lyra/core/agent/**/*.py`, `src/lyra/core/cli/**/*.py` |
| P3 | `src/lyra/core/commands/**/*.py`, `src/lyra/core/stores/**/*.py`, `src/lyra/core/pool/**/*.py`, `src/lyra/core/messaging/**/*.py` |
| P4 | `src/lyra/core/processors/**/*.py`, `src/lyra/core/memory/**/*.py`, `src/lyra/core/auth/**/*.py` |
| P5 | `src/lyra/adapters/**/*.py` |
| P6 | `src/lyra/bootstrap/**/*.py` |
| P7 | `src/lyra/infrastructure/**/*.py`, `src/lyra/nats/**/*.py` |
| P8 | `src/lyra/llm/**/*.py`, `src/lyra/agents/**/*.py`, `src/lyra/*.py`, `src/lyra/config/**/*.py`, `src/lyra/monitoring/**/*.py`, `src/lyra/stt/**/*.py` |
| T1 | `tests/unit/core/**/*.py` |
| T2 | `tests/unit/adapters/**/*.py`, `tests/unit/bootstrap/**/*.py` |
| T3 | `tests/integration/**/*_test.py` (first half alphabetically) |
| T4 | `tests/integration/**/*_test.py` (second half alphabetically) |
| T5 | `tests/e2e/**/*.py`, remaining test files |
| T6 | Coverage report analysis (all tests) |

---

## Domain Prompts

### Architecture (arch-*)

```
## Task
Analyze ARCHITECTURE for {PARTITION} area.

## Files to Analyze
{PATTERNS}

## Focus Areas
- Layer violations (adapters importing core, etc.)
- Circular dependencies
- Module coupling score
- Single responsibility violations
- Dependency direction consistency

## Output
Write findings to: artifacts/analyses/quality-audit/architecture/{OUTPUT_FILE}

## Format
### Summary
[2-3 sentence overview]

### Findings
| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|

### Metrics
- Module coupling: X/10
- Circular deps: N
- Layer violations: N

### Recommendations
[Prioritized list of fixes]
```

### Security (sec-*)

```
## Task
Analyze SECURITY for {PARTITION} area.

## Files to Analyze
{PATTERNS}

## Focus Areas
- Hardcoded secrets/credentials
- SQL injection vectors
- Command injection
- Path traversal
- Insecure deserialization
- Missing input validation
- Logging sensitive data

## Output
Write findings to: artifacts/analyses/quality-audit/security/{OUTPUT_FILE}

## Format
### Summary
[2-3 sentence overview]

### Findings
| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|

### OWASP Coverage
| Category | Issues Found |
|----------|--------------|

### Recommendations
[Prioritized list of fixes]
```

### Code Smells (smell-*)

```
## Task
Analyze CODE SMELLS for {PARTITION} area.

## Files to Analyze
{PATTERNS}

## Focus Areas
- Functions > 50 lines
- Classes > 300 lines
- God classes (≥10 methods)
- Code duplication (DRY violations)
- Long parameter lists (>5 params)
- Deep nesting (>4 levels)

## Output
Write findings to: artifacts/analyses/quality-audit/code-smells/{OUTPUT_FILE}

## Format
### Summary
[2-3 sentence overview]

### Findings
| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|

### Metrics
- Avg function length: X lines
- Max function length: X lines
- God classes: N
- Duplication hotspots: N

### Recommendations
[Prioritized list of fixes]
```

### Type Safety (type-*)

```
## Task
Analyze TYPE SAFETY for {PARTITION} area.

## Files to Analyze
{PATTERNS}

## Focus Areas
- Missing type hints on public APIs
- `Any` type usage
- `# type: ignore` comments
- Optional handling gaps
- Generic type misuse

## Output
Write findings to: artifacts/analyses/quality-audit/type-safety/{OUTPUT_FILE}

## Format
### Summary
[2-3 sentence overview]

### Findings
| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|

### Metrics
- Type coverage: X%
- `Any` usage: N instances
- `type: ignore`: N instances

### Recommendations
[Prioritized list of fixes]
```

### Async Patterns (async-*)

```
## Task
Analyze ASYNC PATTERNS for {PARTITION} area.

## Files to Analyze
{PATTERNS}

## Focus Areas
- Blocking calls in async functions
- Missing `await` keywords
- Race conditions
- Resource leaks (unclosed connections)
- Improper exception handling in async
- Deadlock potential

## Output
Write findings to: artifacts/analyses/quality-audit/async-patterns/{OUTPUT_FILE}

## Format
### Summary
[2-3 sentence overview]

### Findings
| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|

### Metrics
- Async functions: N
- Blocking calls in async: N
- Potential race conditions: N

### Recommendations
[Prioritized list of fixes]
```

### Error Handling (err-*)

```
## Task
Analyze ERROR HANDLING for {PARTITION} area.

## Files to Analyze
{PATTERNS}

## Focus Areas
- Bare `except:` clauses
- Swallowed exceptions (pass in except)
- Generic Exception catches
- Missing error context
- Inconsistent error propagation
- Missing finally blocks for cleanup

## Output
Write findings to: artifacts/analyses/quality-audit/error-handling/{OUTPUT_FILE}

## Format
### Summary
[2-3 sentence overview]

### Findings
| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|

### Metrics
- Try/except blocks: N
- Bare excepts: N
- Swallowed exceptions: N

### Recommendations
[Prioritized list of fixes]
```

### Test Quality (test-*)

```
## Task
Analyze TEST QUALITY for {PARTITION} area.

## Files to Analyze
{PATTERNS}

## Focus Areas
- Coverage gaps (<80% on critical paths)
- Missing edge case tests
- Flaky test patterns
- Test smells (sleep, hardcoded ports)
- Missing assertion messages
- Over-mocking

## Output
Write findings to: artifacts/analyses/quality-audit/test-quality/{OUTPUT_FILE}

## Format
### Summary
[2-3 sentence overview]

### Findings
| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|

### Metrics
- Test files: N
- Test functions: N
- Flaky patterns: N
- Mock usage: N

### Recommendations
[Prioritized list of fixes]
```

### Tech Debt (tech-*)

```
## Task
Analyze TECH DEBT for {PARTITION} area.

## Files to Analyze
{PATTERNS}

## Focus Areas
- TODO/FIXME comments
- Deprecated API usage
- Dead code (unreachable, unused)
- Commented-out code
- Magic numbers/strings
- Inconsistent naming patterns

## Output
Write findings to: artifacts/analyses/quality-audit/tech-debt/{OUTPUT_FILE}

## Format
### Summary
[2-3 sentence overview]

### Findings
| File | Line | Issue | Severity | Recommendation |
|------|------|-------|----------|----------------|

### Metrics
- TODOs: N
- FIXMEs: N
- Dead code lines: N
- Deprecated patterns: N

### Recommendations
[Prioritized list of fixes]
```

---

## Synthesis Prompt

```
## Task
Synthesize all audit findings into a unified report.

## Input Files
Read all files in:
- artifacts/analyses/quality-audit/architecture/
- artifacts/analyses/quality-audit/code-smells/
- artifacts/analyses/quality-audit/type-safety/
- artifacts/analyses/quality-audit/security/
- artifacts/analyses/quality-audit/async-patterns/
- artifacts/analyses/quality-audit/error-handling/
- artifacts/analyses/quality-audit/test-quality/
- artifacts/analyses/quality-audit/tech-debt/

## Output
Write to: artifacts/analyses/quality-audit/AUDIT-SUMMARY.md

## Format
# Code Quality Audit Summary

## Executive Summary
[3-5 bullet points on overall codebase health]

## Critical Issues (P0)
[Issues requiring immediate attention - security vulns, data loss risks]

## High Priority (P1)
[Issues to address in next sprint - bugs, significant tech debt]

## Medium Priority (P2)
[Issues for backlog - refactorings, improvements]

## Low Priority (P3)
[Nice-to-haves - minor cleanups]

## Metrics Dashboard
| Domain | Issues | P0 | P1 | P2 | P3 |
|--------|--------|----|----|----|----|
| Architecture | | | | | |
| Security | | | | | |
| Code Smells | | | | | |
| Type Safety | | | | | |
| Async Patterns | | | | | |
| Error Handling | | | | | |
| Test Quality | | | | | |
| Tech Debt | | | | | |
| **Total** | | | | | |

## Recommended Actions
[Prioritized list with effort estimates]

## Technical Debt Score
[Aggregate debt metric 0-100, where 100 = pristine]

## Top 10 Quick Wins
[Actions with high impact, low effort]
```
