@.claude/stack.yml
@~/.claude/shared/global-patterns.md

# CLAUDE.md — Instructions for Claude Code

## Project

**Lyra by Roxabi** — Personal AI agent engine (hub-and-spoke, asyncio, multi-channel).
See `docs/ARCHITECTURE.md` for full context.

## TL;DR

- **Project:** Lyra
- **Before work:** Use `/dev #N` as the single entry point — it determines tier (S / F-lite / F-full) and drives the full lifecycle
- **Decisions:** → see global patterns (@~/.claude/shared/global-patterns.md)
- **Never** use `--force`/`--hard`/`--amend`
- **Always** use appropriate skill even without slash command

## Key files

| File | Role |
|------|------|
| `docs/ARCHITECTURE.md` | Architecture + technical decisions |
| `docs/CONFIGURATION.md` | All config files, their purpose, load order, and system vs instance split |
| `docs/ROADMAP.md` | Roadmap and priorities |
| `docs/GETTING-STARTED.md` | Machine 1 setup guide |
| `artifacts/` | Frames, specs, plans, analyses, explorations (dev-core) |
| `deploy/provision.sh` | Machine 1 post-install provisioning script |
| `deploy/quadlet/` | Podman Quadlet units (`.container`, `.volume`, `.network`) — systemd-integrated containers; `lyra-nats-auth.volume` for NATS public auth.conf |
| `deploy/nats/nats-container.conf` | NATS config for container deployment (no TLS, 0.0.0.0 bind) |
| `scripts/dep-graph/` | GitHub-driven dep-graph generator (`dep_graph/` package + `layout.schema.json`). Run via `make dep-graph [fetch\|build\|audit\|validate\|open]`. Artifacts (layout.json, gh.json cache, HTML output) live in `~/.roxabi/forge/lyra/visuals/`. Precursor to roxabi-dashboard. |
| `packages/roxabi-nats/` | NATS transport SDK (uv workspace subpackage) — adapter_base, connect, circuit_breaker, readiness, serialization. Extracted per ADR-045. |

## Local infrastructure

Machine data (IPs, partitions, configs) lives in **`local/machines.md`** (gitignored, not versioned).

Check this file for:
- Machine IPs and hostnames
- Disk layouts
- Useful SSH commands
- Active services

```bash
# Connect to Machine 1 (Hub)
ssh mickael@192.168.1.16
```

## Machines

- **Machine 1** (`roxabituwer`, `192.168.1.16`) — Hub, Ubuntu Server 24.04, RTX 3080, 24/7
- **Machine 2** (`ROXABITOWER`) — AI Server, Windows + WSL2, RTX 5070Ti, on-demand

## Agent management

Agents live in **`~/.lyra/auth.db`** (SQLite). TOML files are seed sources only — they must be imported into the DB via `lyra agent init` before startup uses them.

Two TOML search locations (in precedence order):
1. `~/.lyra/agents/` — user-level overrides (machine-specific, gitignored)
2. `src/lyra/agents/` — bundled system defaults

```bash
lyra agent init           # seed DB from TOML files (first-time or after TOML edits)
lyra agent init --force   # overwrite existing DB rows
lyra agent list           # list all agents in DB
lyra agent show <name>    # full config for one agent
lyra agent edit <name>    # edit interactively in DB (no TOML needed)
lyra agent validate <name>
lyra agent create <name>                       # scaffold a new agent TOML
lyra agent assign <name> --platform telegram --bot <bot_id>
lyra agent unassign --platform telegram --bot <bot_id>
lyra agent delete <name>  # refuses if bot still assigned
```

- `cwd` is machine-specific → lives in `config.toml [defaults]`, NOT in agent TOML.
- `workspaces` — each key becomes accessible via `/workspace <key>` (not `/<key>`), overriding cwd for the current pool. See `docs/COMMANDS.md`.
- TOML edits → `lyra agent init --force` + restart to take effect.

## Conventions

- Language: English for all docs, code and commits
- Commits: Conventional Commits (`feat:`, `fix:`, `chore:`, etc.)
- Issues: via `dev-core` workflow (`/dev #N`)

## CLAUDE.md hygiene (hard rule)

**When you add, remove, or rename a file/package, update the relevant CLAUDE.md immediately.**

CLAUDE.md locations (one per package, single level — no nested sub-CLAUDE.md):
- `CLAUDE.md` — project root (this file)
- `src/lyra/core/CLAUDE.md` — hub, stores, pool, commands infra + all flat modules
- `src/lyra/adapters/CLAUDE.md` — Telegram, Discord, CLI, NATS adapters
- `src/lyra/agents/CLAUDE.md` — agent implementations
- `src/lyra/commands/CLAUDE.md` — plugin commands
- `src/lyra/llm/CLAUDE.md` — LLM drivers and providers

Rules:
- New file added → add it to the file table in the appropriate CLAUDE.md
- File deleted → remove its entry
- File moved → update source and destination CLAUDE.md
- New package/subdir under `src/lyra/` → add to the nearest CLAUDE.md (do NOT create a new nested CLAUDE.md)

## Production entry points (NATS three-process mode)

Lyra runs as three separate supervisor processes on Machine 1:

| Supervisor program | CLI command | Bootstrap function |
|-------------------|-------------|-------------------|
| `lyra_hub` | `lyra hub` | `_bootstrap_hub_standalone()` |
| `lyra_telegram` | `lyra adapter telegram` | `_bootstrap_adapter_standalone()` |
| `lyra_discord` | `lyra adapter discord` | `_bootstrap_adapter_standalone()` |

Scripts: `run_hub.sh` and `run_adapter.sh` in `deploy/supervisor/scripts/` (lyra's own supervisord, managed via `lyra.service`).
NATS topics: `lyra.inbound.<platform>.<bot_id>` (adapter→hub) · `lyra.outbound.<platform>.<bot_id>` (hub→adapter).

The unified single-process mode (`lyra start` → `_bootstrap_unified`) runs hub + adapters in one process with NATS. Auto-starts embedded nats-server when NATS_URL is not set.

## Gotchas

<!-- Add project-specific gotchas here -->

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **lyra** (16291 symbols, 37698 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## When Debugging

1. `gitnexus_query({query: "<error or symptom>"})` — find execution flows related to the issue
2. `gitnexus_context({name: "<suspect function>"})` — see all callers, callees, and process participation
3. `READ gitnexus://repo/lyra/process/{processName}` — trace the full execution flow step by step
4. For regressions: `gitnexus_detect_changes({scope: "compare", base_ref: "main"})` — see what your branch changed

## When Refactoring

- **Renaming**: MUST use `gitnexus_rename({symbol_name: "old", new_name: "new", dry_run: true})` first. Review the preview — graph edits are safe, text_search edits need manual review. Then run with `dry_run: false`.
- **Extracting/Splitting**: MUST run `gitnexus_context({name: "target"})` to see all incoming/outgoing refs, then `gitnexus_impact({target: "target", direction: "upstream"})` to find all external callers before moving code.
- After any refactor: run `gitnexus_detect_changes({scope: "all"})` to verify only expected files changed.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Tools Quick Reference

| Tool | When to use | Command |
|------|-------------|---------|
| `query` | Find code by concept | `gitnexus_query({query: "auth validation"})` |
| `context` | 360-degree view of one symbol | `gitnexus_context({name: "validateUser"})` |
| `impact` | Blast radius before editing | `gitnexus_impact({target: "X", direction: "upstream"})` |
| `detect_changes` | Pre-commit scope check | `gitnexus_detect_changes({scope: "staged"})` |
| `rename` | Safe multi-file rename | `gitnexus_rename({symbol_name: "old", new_name: "new", dry_run: true})` |
| `cypher` | Custom graph queries | `gitnexus_cypher({query: "MATCH ..."})` |

## Impact Risk Levels

| Depth | Meaning | Action |
|-------|---------|--------|
| d=1 | WILL BREAK — direct callers/importers | MUST update these |
| d=2 | LIKELY AFFECTED — indirect deps | Should test |
| d=3 | MAY NEED TESTING — transitive | Test if critical path |

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/lyra/context` | Codebase overview, check index freshness |
| `gitnexus://repo/lyra/clusters` | All functional areas |
| `gitnexus://repo/lyra/processes` | All execution flows |
| `gitnexus://repo/lyra/process/{name}` | Step-by-step execution trace |

## Self-Check Before Finishing

Before completing any code modification task, verify:
1. `gitnexus_impact` was run for all modified symbols
2. No HIGH/CRITICAL risk warnings were ignored
3. `gitnexus_detect_changes()` confirms changes match expected scope
4. All d=1 (WILL BREAK) dependents were updated

## Keeping the Index Fresh

After committing code changes, the GitNexus index becomes stale. Re-run analyze to update it:

```bash
npx gitnexus analyze
```

If the index previously included embeddings, preserve them by adding `--embeddings`:

```bash
npx gitnexus analyze --embeddings
```

To check whether embeddings exist, inspect `.gitnexus/meta.json` — the `stats.embeddings` field shows the count (0 means no embeddings). **Running analyze without `--embeddings` will delete any previously generated embeddings.**

> Claude Code users: A PostToolUse hook handles this automatically after `git commit` and `git merge`.

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
