# AGENTS.md — factory (roxabi-factory)

Let:
  A := ~/.roxabi/factory/auth.db (grants, identity only) | C := ~/.roxabi/factory/config.db (agents, bots, prefs) | T := TOML seed | P := CLAUDE.md path (shim → `@AGENTS.md`)

## Project

**factory** — AI factory engine (hub-spoke, asyncio, multi-channel)
→ `docs/ARCHITECTURE.md`

## TL;DR

- Entry: `/dev #N` → tier (S/F-lite/F-full) → lifecycle
- Close checklist (pre-cleanup, from worktree): `docs/process/dev-cycle.md` — run **before** worktree removal; `/dev` skill integration pending (roxabi-plugins)
- ¬`--force` | ¬`--hard` | ¬`--amend`

## Axial Review (mandatory)

PRs that cross architectural layer boundaries MUST carry `dev-core:axial-adr-review` before merge.
`.github/workflows/axial-review.yml` applies labels automatically; do NOT strip them manually.

| Trigger | Labels applied |
|---------|---------------|
| PR touches top-level `inbound/` **and** top-level `adapters/` | `dev-core:axial-adr-review` |
| PR touches top-level `core/` **and** top-level `infrastructure/` | `dev-core:axial-adr-review` |
| PR adds `except Exception` (any binding form: `as e`, `as exc`, bare) | `dev-core:axial-adr-review` + `dev-core:security-auditor` |

Note: `core/ports/` and `core/hub/` are both sub-packages of `core/` — intra-core changes touching both do NOT trigger the label. Only changes that cross the top-level `factory.*` module boundary trigger `dev-core:axial-adr-review`.

Review checklist (applies when `dev-core:axial-adr-review` is present):
- Confirm no inbound-layer logic leaks into adapters (ADR boundary)
- Confirm core domain objects are not polluted with infrastructure concerns
- `except Exception:` must be justified inline with a comment; open a follow-up issue if swallowing

## Key files

| File | Role |
|---|---|
| `docs/ARCHITECTURE.md` | Architecture + decisions |
| `docs/process/dev-cycle.md` | `/dev #N` close checklist — debt retrospective |
| `docs/architecture/CURRENT.generated.md` | Generated inventory SSoT (layers/subjects/topology) — ¬edit, gated by `architecture_snapshot` |
| `docs/CONFIGURATION.md` | Config files, load order |
| `docs/agent-management.md` | Agent seed flow + CLI |
| `docs/bot-management.md` | Bot seed flow + CLI |
| `docs/ops/container-publishing.md` | CI → GHCR → Quadlet pattern |
| `deploy/quadlet/factory-nats.container` | NATS Quadlet unit — `type=mount` secret anchor (restart-not-HUP for ACL changes) |
| `packages/roxabi-nats/` | NATS transport SDK (ADR-045) |
| `packages/roxabi-contracts/` | NATS contract schemas (ADR-049) |
| `src/factory/transport/` | NATS transport + WorkerPoolClient (3-layer composition, #1278) |

## Agent management

Agents ∈ C (SQLite) | T files = seed only → `factory agent init` before use
Search: `~/.roxabi/factory/agents/` (override) → `src/factory/agents/` (default)
`cwd` → `config.toml [defaults]` (¬T)

→ `docs/agent-management.md` — CLI verbs: `init | list | show | edit | patch | validate | create | delete | assign | unassign | refine`

## Agent instructions hygiene

Content lives in `AGENTS.md` (Cursor + agents). `CLAUDE.md` is a thin shim (`@AGENTS.md` + Claude Code `@` imports at root only).

File/rename → update `AGENTS.md` + shim `CLAUDE.md` + registry immediately.

→ `docs/claude-md-registry.md` — full P→scope table (the SSoT; one row per shim). Update there on add/rename/delete.

Rules: add/delete/move → update `AGENTS.md` + `CLAUDE.md` shim + register | new subdir with non-obvious invariants → add both + register | "invariants, not inventory" (¬file counts, ¬method dumps — let `ls`/`grep` answer that)

## Production entry points

Each prod NATS process is a `factory <subcommand>` whose composition root is a
`_bootstrap_*_standalone()` in `src/factory/bootstrap/standalone/`. The authoritative
set of deployed processes is the enabled `[component.*]` sections in
`deploy/quadlet.toml`; enumerate the CLI surface with `factory --help` or
`git grep -nE '@(adapter_app|hub_app)\.command|add_typer' src/factory/cli/main.py`
(hub, `adapter {telegram,discord,web,clipool,omp}`, `turn-writer`, `ingress serve`,
`blobstore serve`, socialmedia-adapter — invariants, not a hand-maintained count).

Topics: `factory.inbound.<platform>.<bot_id>` | `factory.outbound.<platform>.<bot_id>`

Unified: `factory start` → hub + adapters in 1 process + embedded NATS

## Container deployment

Prod: Podman Quadlet (systemd `--user`) on M₁ (`factory-hub` role). The deployed
container set is the enabled `[component.*]` sections in `deploy/quadlet.toml` (SSoT;
Langfuse + `factory-otel-collector` ship disabled) — see also
`docs/architecture/CURRENT.generated.md § topology`. Install: `deploy/install.sh`
(idempotent).

Plus the llmCLI cloud-gateway units vendored from Roxabi/llmCLI (LiteLLM proxy
:18091 + xAI/Grok forwarder :18645 + Fireworks forwarder :18646 — ports are the
contract) — deployment owned here (M₁ always-on cloud LLM gateway), image built +
published by llmCLI CI and pinned by digest. Local GPU inference (llmcli-nats-worker,
M₂) stays in Roxabi/llmCLI. See `deploy/AGENTS.md` § llmCLI cloud gateway.

→ `docs/runbooks/README.md` — ops runbooks (install, secrets, diagnostic)
→ `~/projects/docs/container-deployment-standard.md` — deployment standards (S7 secret target, S8 naming, S12 RestartSec=10)
