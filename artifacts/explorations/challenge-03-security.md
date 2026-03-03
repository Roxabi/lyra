# Challenge #3 — Security: Sandbox, Prompt Injection & Audit

> Challenge document based on knowledge base data.
> Last updated: 2026-03-02

---

## Our current plan

4 security mechanisms:
- Prompt injection guard (content validation before agent context)
- Immutable hash-chained audit trail
- Least privilege (each skill declares its permissions)
- Third-party skill signatures (integrity verification at load time)

---

## What the knowledge base brings

### 1. Browser Use — sandbox architecture for agents in production

**Source**: [@larsencc, Twitter](https://x.com/larsencc/status/2027225210412470668)

Browser Use (millions of web agents) migrated their sandboxing:
- **Pattern 1** (abandoned): isolate the tool
- **Pattern 2** (adopted): isolate the entire agent in a Unikraft micro-VM

Final architecture:
- Sandboxes receive **exactly 3 environment variables**
- The FastAPI control plane proxies all external requests
- Startup in under one second
- No secrets in the sandbox

**What this challenges**: our "sandboxing" (limited environment variables, restricted filesystem) remains vague in implementation. Browser Use shows that real isolation = micro-VM, not file restrictions.

For Lyra personal, micro-VMs are excessive. But the pattern "control plane as proxy for all external requests" is useful and applicable.

### 2. 10 engineering principles for agents in production

**Source**: [@rohit4verse, Twitter](https://x.com/rohit4verse/status/2022709729450201391)

40% of AI agent projects fail due to poor architecture. Key principles:

1. **Threat modeling** from the start
2. **Prompt injection defense** = vulnerability #1 in 73% of deployments
3. **Strict typed contracts** for all tool schemas + server-side validation
4. No unvalidated dynamic tools

**What this calls into question**: our prompt injection guard is mentioned but not specified. "Content validation before agent context" is vague. In production, 73% of vulnerabilities come from this.

### 3. The 25 anti-patterns of a vibe-coded app

**Source**: [@Hartdrawss, Twitter](https://x.com/Hartdrawss/status/2028040114908135575)

Critical anti-patterns:
- Hardcoded API keys
- No DB migrations
- No monitoring
- CORS wildcard
- No input validation
- Untested backups

**What directly concerns us**:
- No input validation on incoming messages (prompt injection)
- No backup strategy for SQLite
- No credential monitoring (Telegram token, Anthropic key)

### 4. Agent Swarm Accountability — task ledger and completion proofs

**Source**: [@jumperz, Twitter](https://x.com/jumperz/status/2028069917690343672)

System for Discord agents that solves: visibility and control in swarms.
- Unique task IDs
- Access-controlled dispatch
- Proof-of-completion requirements
- Automatic watcher to enforce rules
- Prevents silent failures, ghost delegations, board drift

**What this brings**: our hash-chained audit trail covers auditability. But not **silent failures** (skills that fail silently) nor **ghost delegations** (skills that believe they completed without actually doing so).

---

## Gap Analysis

| Mechanism | Planned | Actual implementation | Risk |
|-----------|---------|----------------------|------|
| Prompt injection guard | Yes | Vague | Vulnerability #1 in prod |
| Hash-chained audit trail | Yes | Not technically specified | False sense of security |
| Least privilege | Yes | Manual (SKILL.md) | Not enforced |
| Third-party skill signatures | Yes | Not implemented in Phase 1 | OK if no third parties |
| SQLite backup | No | Missing | Data loss |
| Silent failure detection | No | Missing | Invisible degradation |
| Secret management | No | Basic .env | Hardcoded keys |

---

## Recommendations

### Priority 1 — Prompt injection guard (specify)
- Structured validation: max length, suspicious content (classic injections)
- No LLM to validate the LLM (pointless loop)
- Simple and fast rules: pattern blocklist, character whitelist if relevant

### Priority 2 — Automatic SQLite backup
- Daily WAL backup -> dated copy (3 lines of bash + cron)
- Test restoration at least once

### Priority 3 — Secret management
- Centralize all secrets in an encrypted `.env` (no hardcoding)
- Easy rotation if compromised

### Priority 4 — Silent failure detection
- Each skill must return a structured result (success/failure/partial)
- The hub logs every outcome

---

## Verdict

Our security plan is well-intentioned but under-specified on the critical points. Prompt injection is vulnerability #1 and it is the first thing to implement precisely, not vaguely. SQLite backup is a non-negotiable that takes 10 minutes.
