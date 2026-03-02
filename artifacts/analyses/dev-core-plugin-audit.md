# Dev-Core Plugin Audit: Genericity & Architectural Pattern Gaps

**Date:** 2026-03-01
**Scope:** All 6 Roxabi plugins installed in Lyra — deep focus on dev-core

---

## Executive Summary

The dev-core plugin is a **production-grade development lifecycle orchestrator** with 19 skills, 9 agents, and 3 safety hooks. Its process automation is excellent (gate-driven, resumable, well-documented). However, it has two critical problems:

1. **Hardcoded to a specific tech stack** — not the generic toolkit it appears to be
2. **Zero architectural pattern knowledge** — no DDD, hexagonal, CQRS, event sourcing, or clean architecture

The other 5 plugins (1b1, compress, memory-audit, web-intel, voice-me) are genuinely generic.

---

## Problem 1: Hardcoded Stack

Every dev-core agent assumes a single monorepo project's technology choices:

| Layer | Hardcoded Tech | Where Embedded |
|-------|---------------|----------------|
| Runtime | Bun (enforced via hook) | hooks.json, all skills |
| Backend | NestJS + Fastify + Drizzle ORM | backend-dev agent, tester agent |
| Frontend | TanStack Start + React + @repo/ui | frontend-dev agent |
| Database | PostgreSQL (Drizzle migrations, branch DBs) | backend-dev, implement skill |
| Build | TurboRepo | devops agent |
| Lint/Format | Biome (auto-format hook) | hooks.json, devops agent |
| Docs | Fumadocs + MDX | doc-writer agent |
| Deploy | Vercel | devops agent, promote skill |
| Monorepo | `apps/api`, `apps/web`, `packages/ui`, `packages/config`, `packages/types` | All agents |

**Impact:** Using these agents on a project with a different stack (e.g., Express, SvelteKit, Prisma, pnpm) will produce wrong code, wrong file paths, wrong commands, and wrong patterns.

---

## Problem 2: Missing Architectural Patterns

The architect agent — the one that should enforce design patterns — contains only:
- ADR creation
- Tier classification (S/F-lite/F-full)
- Cross-package consistency checks

**Completely absent:**

| Pattern | What's Missing |
|---------|---------------|
| **DDD Tactical** | Aggregates, value objects, entities, domain events, repositories, domain services |
| **DDD Strategic** | Bounded contexts, context mapping, ubiquitous language enforcement |
| **Hexagonal / Ports & Adapters** | Port interfaces, adapter implementations, dependency inversion rules |
| **Clean Architecture** | Layer dependency rules (domain → application → infrastructure), use case boundaries |
| **CQRS** | Command/query separation, read models vs write models |
| **Event Sourcing** | Event stores, projections, event replay |
| **Contract Testing** | OpenAPI/AsyncAPI validation, consumer-driven contracts |
| **Multi-tenancy** | Tenant isolation strategies, data partitioning |

The backend-dev agent has one useful rule (`domain exceptions = pure TS, no NestJS imports`) which hints at hexagonal thinking but never names or enforces the pattern.

---

## Agent-by-Agent Assessment

### architect
- **Role:** System design, ADRs, tier classification
- **Stack dependency:** Low (references `docs/architecture/` generically)
- **Pattern knowledge:** None — purely process-oriented
- **Verdict:** Empty vessel. Does coordination, not architecture.

### backend-dev
- **Role:** API implementation
- **Stack dependency:** Critical — NestJS modules, Fastify, Drizzle, `apps/api/` paths
- **Pattern knowledge:** Module-per-feature, controllers=HTTP-only, pure-TS exceptions
- **Missing:** Transaction handling, aggregate boundaries, repository pattern, event publishing
- **Verdict:** Hardcoded to one project. Unusable elsewhere without rewrite.

### frontend-dev
- **Role:** UI implementation
- **Stack dependency:** Critical — TanStack Start, @repo/ui, `cn()` utility, `packages/ui/`
- **Pattern knowledge:** Component composition, co-located tests
- **Missing:** State management strategy, form patterns, error boundaries
- **Verdict:** Same — project-specific.

### tester
- **Role:** Test generation and validation
- **Stack dependency:** High — Vitest imports, NestJS `Test.createTestingModule()`, Drizzle mock factories
- **Pattern knowledge:** Testing Trophy (integration > unit), AAA pattern, coverage rules
- **Missing:** Contract testing, mutation testing, load testing
- **Verdict:** Good testing philosophy, but NestJS/Drizzle patterns are hardcoded.

### devops
- **Role:** Infrastructure, CI/CD, deps
- **Stack dependency:** Critical — Bun, TurboRepo, Biome, Docker, Vercel
- **Verdict:** Entirely project-specific.

### doc-writer
- **Role:** Documentation
- **Stack dependency:** High — Fumadocs conventions, MDX rules, `meta.json` format
- **Verdict:** Fumadocs-specific. Generic MDX knowledge buried under project conventions.

### security-auditor
- **Role:** OWASP vulnerability scanning
- **Stack dependency:** Low — generic OWASP checklist, `bun audit` only specific part
- **Verdict:** Most generic agent. Mostly reusable.

### product-lead
- **Role:** Requirements, specs, backlog
- **Stack dependency:** Low — GitHub issues, artifacts paths
- **Verdict:** Mostly generic process. Reusable.

### fixer
- **Role:** Apply review findings
- **Stack dependency:** Low — delegates to domain agents
- **Verdict:** Generic. Reusable.

---

## Other Plugins Assessment

| Plugin | Generic? | Notes |
|--------|----------|-------|
| **1b1** | Yes | Walk-through items one-by-one. No stack assumptions. |
| **compress** | Yes | Notation compression utility. Pure text transformation. |
| **memory-audit** | Yes | Claude Code memory cleanup. No stack dependency. |
| **web-intel** | Yes | URL scraping/analysis. Python-based but self-contained. |
| **voice-me** | Yes | TTS/voice generation. Self-contained. |

---

## Quality Scores

| Dimension | Score | Notes |
|-----------|-------|-------|
| Process Definition | 9/10 | Gate-driven, resumable, well-documented |
| Safety & Guardrails | 9/10 | Hooks, verification gates, confidence thresholds |
| Documentation | 8/10 | Verbose but complete (some references unwritten) |
| Technology Coverage | 4/10 | Single-stack, not parameterized |
| Architectural Patterns | 2/10 | Nearly zero pattern knowledge |
| Reusability | 3/10 | Agents are project-specific, skills are mostly generic |

---

## Structural Issue: Generic vs Project-Specific

The plugin conflates two concerns:

```
dev-core (plugin)
├── Skills (process)     → Mostly generic (frame, spec, plan, review...)
├── Agents (execution)   → Mostly project-specific (backend-dev, frontend-dev, devops...)
├── References (knowledge) → Project-specific (dev-process, team-coordination)
└── Hooks (guardrails)   → Project-specific (bun enforcement, biome format)
```

The **skills** (workflow orchestration) are largely reusable. The **agents** (execution) are not. These should be separate layers.

---

## Recommendations

### Option A: Make dev-core truly generic
- Extract stack-specific config into per-project `CLAUDE.md` or `.claude/stack.yml`
- Agents read stack config at runtime instead of hardcoding
- Architectural patterns become loadable reference modules
- Hooks become opt-in per project

### Option B: Fork for Lyra
- Copy dev-core, strip the hardcoded stack
- Replace with Lyra's actual stack and architecture decisions
- Add DDD/hexagonal/CQRS pattern references as needed
- Keep skills mostly as-is (they're good)

### Option C: Layer separation (recommended)
- **dev-core** stays as process-only (skills + generic agents like product-lead, fixer, security-auditor)
- **Stack packs** (new plugin type) provide tech-specific agents + hooks + references
- **Architecture packs** provide pattern knowledge (DDD pack, hexagonal pack, etc.)
- Projects compose: `dev-core` + `stack-nestjs` + `arch-ddd` or `dev-core` + `stack-django` + `arch-hexagonal`

### For Lyra specifically
Before any structural change, Lyra needs:
1. A `CLAUDE.md` defining its own stack, architecture, and conventions
2. Decisions on which patterns to adopt (DDD? hexagonal? CQRS?)
3. Then either fork dev-core agents or build a Lyra-specific stack pack

---

## Minor Issues

- **README.md** claims 17 skills, actual count is 19
- **Reference docs partially unwritten:** `edge-cases.md` (plan + implement), `templates.md` (interview), `expert-consultation.md` (analyze + spec), `smart-splitting.md` (spec), `release-artifacts.md` (promote) — exist but were not verified as complete
- **All agents use Sonnet** — no cost optimization for simple tasks (haiku could handle fixer, doc-writer)
- **No model override mechanism** — can't switch agent models per project
