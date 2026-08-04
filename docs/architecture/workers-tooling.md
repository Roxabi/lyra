---
title: Workers & Tooling — factory
description: Living reference for the tool taxonomy and runtime vocabulary (workerEngine/harness/worker/provider/satellite), runtime workers (CliPool), tool integration patterns, processor registry, and dev tooling enforcement.
---

# Workers & Tooling — factory

> Status: LIVING — current truth for the tool model, workers, pools, tool registries, dev tooling.
> Last updated: 2026-08-04. Absorbed `tool-architecture.md` (taxonomy + runtime vocabulary), 2026-07-02; admission A/B/C/D 2026-08-04.
> Source ADRs: 010, 019, 061, 071. Archived to `adr/archive/` (superseded, absorbed here): 004, 005, 006, 007, 026, 030, 031, 038.

## Scope

Two altitudes in one page. **Conceptual**: what a tool / worker / harness / provider *is* — the 5-layer tool taxonomy, the domain-nature split, and the two discriminators that keep the vocabulary sharp (distilled from the #493 analyses; design locked D1-D16, 2026-06-02). **Implementation**: runtime workers (CliPool subprocess pool), tool integration patterns (external CLIs, session providers, processor registry), and dev tooling enforcement (importlinter contracts, health monitoring layer boundaries). Excludes adapters (`adapters.md`), NATS wire schemas (`contracts.md`), security mechanisms (`security-routing.md`), and deployment infra (`deployment.md`).

## Current state

### Tool model & runtime vocabulary

#### The layers

```
worker  (a compute instance)
  └─ runs ─▶ workerEngine  =  coded pipeline (deterministic workflow)
                │ calls, as steps:
                ├─▶ harness            pure agentic turn (LLM + in-turn tools) · no pipeline logic
                ├─▶ tools  ──backed by──▶ provider ──(if self-hosted)──▶ satellite
                └─▶ internal code
```

A **workerEngine** orchestrates a deterministic workflow; it *calls* the agentic primitive (**harness**), **tools**, and **internal code** as steps. A **harness** runs one pure agentic turn (the LLM decides; tools dispatched within the turn) with **no** workflow logic — it is a *callee*, not a flavor, of the workerEngine. The two are **layered, not unified** (harness = #1490; workerEngine ≈ #1044 code-worker runtime).

#### Runtime vocabulary (canonical glossary)

| Term | Nature | Definition |
|---|---|---|
| **workerEngine** | plomberie · runtime | coded pipeline (deterministic); orchestrates harness + tools + code |
| **harness** | plomberie · agentic | one pure agentic turn (LLM + in-turn tools); no pipeline logic; callee of workerEngine |
| **worker** | plomberie · compute | a deployed instance running a workerEngine; consumes tools |
| **tool** | **tool-nature** | a capability surface invoked by harness / workerEngine |
| **provider** | plomberie · backend | the **role** that backs ≥1 tool; transport-agnostic |
| **satellite** | plomberie · deployment | a provider deployed as a self-hosted NATS process with heartbeat (⊂ provider) |
| **backing service** | infra | the self-hosted app behind a provider (e.g. the Postiz stack); not a tool/provider/satellite itself |

> "worker" was historically overloaded — the #493 analysis used it for tool **providers**. Resolved: **worker = compute-on-engine** (consumer); the tool-backing things are **providers**. The code-side rename is incomplete: the hub-side registry that consumes satellite heartbeats is still named `WorkerRegistry`.

#### Taxonomy A — the 5-layer tool model

One tool, five layers, **one uniform dispatcher** as the design target (one ACL gate, one result type, transport varies — the LLM sees a built-in bash and a remote image-generation tool identically). Full dispatcher rollout remains incremental.

| Layer | Name | What | Transport |
|---|---|---|---|
| **L0a** | Built-in atomic | compiled in the engine, in-process | — |
| **L0b** | Remote atomic | a provider on the bus, discovered by heartbeat | NATS / stdio / http |
| **L1** | Macro | deterministic chain of primitives, no second LLM | in-proc or remote |
| **L2** | Skill | instruction-layer composite (SKILL.md) guiding the LLM | prompt injection |
| **L3** | Sub-agent | nested isolated agentic call, returns a summary | NATS harness |

#### Taxonomy B — domain-nature (NATS contract domains)

Wire domains split by **nature**:

| | Domains |
|---|---|
| **tool** (capability surface) | voice · image · socialmedia · +future (xcli, vault, scrape, camoufox) |
| **plomberie** (substrate / infra) | cli/clipool · llm · jobs · gh · turns · outbound · event · audit · dashboard · state · telemetry · verify · fleet |

The canonical tool-nature subject shape carries a `tool.` infix — live for the socialmedia domain: `factory.tool.socialmedia.>` (hub ↔ socialmedia satellite, per `deploy/nats/acl-matrix.json`), e.g. `factory.tool.socialmedia.publish` and `factory.tool.socialmedia.heartbeat`. The voice and image domains predate the infix and keep their original prefixes on the wire (`factory.voice.tts.request`, `factory.image.generate.request`). Plomberie domains keep plain `factory.<domain>.*` shapes. Wire schemas live per-domain in `packages/roxabi-contracts/` (e.g. `roxabi_contracts.socialmedia`, `roxabi_contracts.voice`); a shared tool *protocol* layer (in-process vs remote tool wrappers, tool manifest) is designed but not yet built.

#### The two discriminators (the sharp rules)

**Rule 1 — tool-surface ≠ tool-nature.** A plomberie domain MAY *expose* a tool without *being* tool-nature. The gh domain can expose a PR-listing tool; the llm domain exposes generation — both stay plomberie. Exposing a tool does not promote the wire domain.

**Rule 2 — provider ⊋ satellite.** A provider is a *satellite* only when it is a self-hosted process we run on NATS with a heartbeat. Otherwise it is a non-satellite provider:

| Backing | Provider shape | Health model |
|---|---|---|
| self-hosted process we run (imageCLI, voiceCLI, Postiz-via-adapter) | NATS satellite adapter | heartbeat + registry |
| **self-hosted HTTP we run** (target scrape service; socialmedia→Postiz direct) | HTTP provider (in-proc client or thin side-car) | **`GET /ready` + client circuit-breaker** |
| cloud API we don't host (X, GitHub) · one-shot stdio CLI | in-proc HTTP / stdio | CB on call failures (no fleet heartbeat) |

Corollary — **three layers, do not conflate**: a **backing service** (e.g. the self-hosted Postiz fork: web+DB+Redis, HTTP) is infra like Postgres. **A running container ≠ a heartbeat** — the heartbeat comes from *our* NATS adapter (the provider), not the HTTP app. backing service ≠ provider ≠ tool. Shipped instance of the pattern (#1713): `src/factory/adapters/socialmedia/` is the satellite adapter bridging `factory.tool.socialmedia.>` to the Postiz Public API.

HTTPS self-hosted is a **first-class provider shape**, not “cloud only”. Prefer it when there is no GPU multi-worker routing need (see admission checklist below).

#### Process families (A / B / C / D)

Do not call every NATS process a “satellite”. Classify first:

| Family | Role | Examples |
|---|---|---|
| **A — Capability satellite** | Self-hosted **tool/provider** on NATS + heartbeat; one (or few) clear verbs | `voice-stt`, `voice-tts`, `image-worker`, `llm-worker` (fleet façade), `socialmedia-adapter` (bridge), `cortex-memory` |
| **B — Runtime worker** | Runs a **workerEngine** / agent or job runner (not a single tool) | `clipool-worker`, `omp-worker`; projected `code-worker` (Shape B jobs — if built) |
| **C — Plomberie** | Bus, storage, edge, write-side; not tool-nature | hub, channel adapters, `blobstore` (HTTP), `turn-writer`, ingress |
| **D — Client / ops** | Publishes requests or reads telemetry; not a provider | `voice-client`, `llm-operator`, dashboard-reader |

**Hub** is family **C** (composition root). It must not grow family-A engines (playwright, GPU, scrapers). Hub middleware that execs tools is stage-axis debt — see [scrape-placement.md](scrape-placement.md).

#### Capability admission checklist

Before adding a process, container, or NATS identity, answer **yes/no**:

1. Do **we operate** the process (image, secrets, host M₁/M₂)?
2. Does **another fleet process** (not only a local agent) need to call it?
3. Do we need **queue groups / multi-instance routing / nkey ACL / JetStream jobs**?
4. Is there **no** clean HTTP (or in-proc) API already?

| Score | Form |
|---|---|
| **Mostly yes** + single capa + NATS already the fabric | **A** NATS satellite (or keep existing) |
| **Mostly yes** + engine / multi-handler jobs | **B** runtime worker (`factory worker --role=…` style) |
| **No** on “we operate” or “multi-caller fleet” | **Provider in-proc / HTTP client** (D or in-hub port) — not a new satellite |
| Self-hosted HTTP API + health | **HTTP provider** (`/ready` + CB) — **not** a satellite by default |
| Storage / sole-writer / edge | **C** plomberie (HTTP or JetStream as appropriate) |

**Reject by default:** “everything outside the hub is a satellite”; god-box `code` satellites; mounting `~/projects` on hub to fix scrape.

#### Fleet map (admission applied)

| Identity / process | Family | Form | Verdict |
|---|---|---|---|
| voice-stt / voice-tts | A | NATS satellite | keep |
| image-worker | A | NATS satellite | keep |
| llm-worker | A | NATS façade (HTTP backends behind) | keep; HTTP transport optional later |
| socialmedia-adapter | A bridge | NATS → Postiz HTTP | keep while isolation/ACL value; target **HTTPS provider** if thin proxy only |
| cortex-memory | A | NATS today → **API (MCP optional)** | migrate; not hub vault slash |
| clipool / omp | B | NATS runtime worker | keep |
| code-worker (#1044) | B (if built) | job runner, not tool satellite | do **not** use for scrape-only |
| blobstore | C | HTTP + NATS audit/ready | keep (not a tool satellite) |
| turn-writer | C | JetStream sole writer | keep |
| telegram / discord / web / ingress | C | edge adapters | keep |
| hub | C | dispatch / auth / pool | thin; no playwright |
| WebIntel scrape (today) | misplaced A-in-C | hub subprocess | **move** to HTTP provider — [scrape-placement.md](scrape-placement.md) |
| vault-add product | — | hub write side-effect | **removed** 2026-08-01 |

---

### Workers & lifecycle

#### CliPool cwd resolution

The CLI subprocess root is resolved via a `pyproject.toml` anchor walk (`_find_project_root()` in `cli_pool_spawn.py`, overridable by env), not a fixed `.parent.parent...` chain. The anchor pattern is robust to file moves and editable installs; it fails loudly at import time if no `pyproject.toml` is found. The fixed parent-chain pattern is prohibited in any path-resolution code that must survive layout changes. → ADR-004

#### CliPool Claude OAuth token

`factory-clipool.container` injects `CLAUDE_CODE_OAUTH_TOKEN` via `Secret=factory-claude-oauth,type=env,target=CLAUDE_CODE_OAUTH_TOKEN`. The `claude` CLI has no token-file flag; env is the only delivery path. `ANTHROPIC_API_KEY` is explicitly excluded from `_SAFE_ENV_KEYS` (enforced by unit test) to prevent silent rerouting to Console pay-per-token billing. Token rotation is manual, annual. Re-run the Podman #28075 verification recipe after any Podman version bump on M₁. → ADR-071

#### Wildcard binding & per-scope pools

Phase 1 shared one pool (one lock, one CLI subprocess) across all users of a wildcard binding. The amendment to ADR-005 (#125) introduced per-scope pool isolation: `resolve_binding()` synthesises a concrete pool ID from the message's `scope_id` (canonical routing scope computed by the adapter — chat, channel, or thread). Each conversation scope gets its own `Pool` with independent history and lock. Rate limiting remains keyed on `user_id` (not the scope) to prevent bypass. CliPool subprocess isolation and memory-namespace isolation per scope remain open (tracked in #112). → ADR-005 (amended)

#### Multi-bot startup resource sharing

Each active agent gets its own `ProviderRegistry`, built by `_build_per_agent_registry()` (`src/factory/bootstrap/factory/providers.py`) on top of **shared** driver instances — the driver stack is constructed once, registries are per-agent. `CliPool` is a single shared instance: a process-management resource, not a per-agent config resource. `MessageManager` (the i18n message catalog) is currently shared across agents and loaded from the first agent's `i18n_language` in sorted order — per-agent message catalogs are an open refinement. → ADR-019

#### Pool callback wiring

`Pool` holds three callbacks (`_session_resume_fn`, `_session_reset_fn`, `_switch_workspace_fn`), all `None` at construction. `AgentBase.configure_pool()` (default no-op; overridden by `SimpleAgent`) wires them. The hub pool middleware (`middleware_pool.py`) calls `configure_pool()` immediately after pool resolution, before context resolution runs; wiring is idempotent (first writer wins). This eliminates the first-message resume no-op that occurred on daemon restart when session resume ran before any turn had wired the callback. → ADR-026

#### Run-loop error response

When `agent.process()` raises an unexpected exception, the pipeline (`guarded_process_one()` in `pool_processor_exec.py` — relocated from the original hub run loop during ADR-059 hexagonal remediation) sends a user-facing generic error reply resolved through the message catalog, with `GENERIC_ERROR_REPLY` as the fallback string, instead of silently dropping the message. Dispatch failures are absorbed separately by `_safe_dispatch()` and logged. → ADR-006

#### Model-config mismatch

When a send receives a `ModelConfig` that differs from the one used to spawn the existing subprocess entry: the **streaming path** (`cli_pool_streaming.py`) respawns the subprocess (also on system-prompt change). The **non-streaming path** (`cli_pool_send.py`) logs a warning and keeps the old config (Phase 1 behaviour). Full migration of the non-streaming path is gated on the model-selector introduction; session continuity across model changes is an open decision for that phase. → ADR-007

---

### Tool integration

#### External tool pattern

External CLIs (voicecli, imagecli, gws, scraper) follow a 3-layer Install–Wrap–Declare pattern. **Install**: binary on PATH via `uv tool install` or build script; no fork, no vendor. **Wrap**: a thin roxabi-plugins skill (SKILL.md only, no code) teaches Claude when and how to invoke the CLI. **Declare**: the agent config declares the capability through its `permissions` allowlist (seeded from the agent TOML, stored in the config DB); an MCP-server declaration path is a later phase. The skill is reusable across any project with the CLI on PATH. Fork into roxabi-plugins only when the upstream is a static asset, abandoned, or requires deep structural changes. → ADR-010

**Web scrape placement** (hub `WebIntelScraper` subprocess is a known prod gap): target is an HTTP scrape service with `/ready` (or agent tool), not a NATS satellite and not hub-mounted `~/projects`. → [scrape-placement.md](scrape-placement.md). Product `/vault-add` removed 2026-08-01.

#### Tool-provider protocol (session tools)

`ScrapeProvider` and `VaultProvider` are async Protocols defined in `factory.integrations.base`. Concrete implementations (`WebIntelScraper`, `CortexVault`) live in `factory.integrations.web_intel` and `factory.integrations.cortex_vault`. Both are bundled into a `SessionTools` dataclass injected into every `SessionCommandEntry` as a required (non-optional) parameter; the entry types the field abstractly so `factory.core` stays decoupled from the concrete bundle. The historical `session_helpers.py` (which hardcoded subprocess invocations inside core) was deleted. The `commands/search` plugin receives its `VaultProvider` via a module-level injectable set at agent startup — a separate injection path from `SessionCommandEntry`. → ADR-030

#### Processor registry & concurrent outbound dispatch

Slash commands that need conversation history are implemented as `BaseProcessor` subclasses registered via the `@register` decorator against the module-level `registry` singleton in `factory.core.processors.processor_registry`. The pipeline calls `pre()` before `agent.process()` and `post()` after; responses enter pool history through the normal flow. Self-registration happens via imports in `src/factory/core/processors/__init__.py` — a new processor file not listed there is silently invisible. `post()` runs in both branches: inline in the non-streaming path, and via `run_streaming_turn_post()` once the stream is fully consumed in the streaming path (#372). Outbound delivery uses per-scope `asyncio.Lock` fan-out in `outbound_dispatcher.py`: tasks for different scopes run concurrently; tasks within the same scope are ordered. Idle locks are reaped when `_scope_locks` grows past `_SCOPE_REAP_THRESHOLD`. The registry handles post-parse execution; pre-parse tokenization is owned by `CommandParser` (→ `security-routing.md`). → ADR-031

#### Model selection (retired)

Smart complexity-based routing is retired: it is no longer supported on any backend, and agent seeding / `factory agent init` force the smart-routing flag to false (`SmartRoutingConfig` survives only as a config-model shape). The complexity-estimator + routing-table design from the early drafts was removed from code. Model selection is fixed per agent config.

---

### Tooling & invariants

#### Importlinter independence contract

The `shared-modules-independence` contract in `.importlinter` enforces peer isolation between the floating modules `factory.obs`, `factory.errors`, `factory.config`, `factory.integrations`, `factory.monitoring`, and `factory.agent_cmd`. The ADR-061 port-import fix landed: `factory.core.agent` imports `STTProtocol`/`TtsProtocol` from `factory.core.ports`, and the former stt/tts floating modules were dissolved into ports and infrastructure. One documented `ignore_imports` suppression remains: `factory.core.processors.processor_registry` → `factory.integrations.base` — a TYPE_CHECKING import of `SessionTools` that importlinter flags transitively even though it is a permitted floating→core path (#977). → ADR-061

#### Health monitoring layer boundaries

Three observation layers: (1) in-process self-monitoring (circuit breakers, error counts), (2) the health-detail endpoint (aggregated snapshot), (3) external monitor (process alive, endpoint reachable, OS resources). Each layer observes only what it can see directly. The dead-backend timing heuristic was removed — the circuit breaker (`check_circuits`) is the correct signal for LLM backend health. Silent CLI protocol failures (empty stdout, NDJSON parse error, unexpected subprocess exit) must be classified as failed `CliResult`s in `cli_protocol.py` so they route to the circuit breaker's `record_failure()`. Active monitor checks live in `src/factory/monitoring/checks.py`: `check_process`, `check_http_health`, `check_queue_depth`, `check_idle`, `check_circuits`, `check_reaper`, plus disk/inode/NATS-varz checks. → ADR-038

---

## Key invariants

- A worker is **not** a tool; it consumes tools and **runs on** a workerEngine. The harness is **pure agentic** (no workflow logic) and is **called by** the workerEngine — layered, never unified.
- tool-surface ≠ tool-nature: a plomberie domain exposing a tool stays plomberie.
- provider ⊋ satellite: NATS self-hosted + heartbeat → satellite; self-hosted HTTP → `/ready`+CB provider (not satellite by default); cloud/stdio → non-satellite. A running backing container is not a heartbeat.
- New process → run capability admission (A/B/C/D); hub is C and must not host tool engines (scrape/GPU).
- The tool dispatcher target is **uniform**: one ACL gate, one result type, transport-agnostic — no per-kind branched pipeline.
- `_find_project_root()` anchor walk is the only permitted pattern for locating the project root; fixed `.parent` chains are prohibited.
- `ANTHROPIC_API_KEY` must never appear in `_SAFE_ENV_KEYS`; the unit test `test_anthropic_api_key_not_forwarded` is load-bearing.
- `configure_pool()` must run at pool resolution, before context resolution — lazy wiring inside `process()` is insufficient; wiring is idempotent.
- Each agent gets its own `ProviderRegistry` over shared drivers; `CliPool` is the only shared process resource.
- Every `SessionCommandEntry` holds a fully-constructed `SessionTools`; the field is required, not optional.
- `src/factory/core/processors/__init__.py` must import every processor module; a missing import silently gaps the registry.
- `BaseProcessor.post()` runs in both branches — non-streaming (inline) and streaming (via `run_streaming_turn_post()` after the stream is consumed).
- Health checks are layer-bound: the external monitor checks process/endpoint/OS only; circuit breakers own LLM backend health.
- Per-scope pool isolation is active at the hub layer; CliPool subprocess isolation per scope remains open.
- `type=env` is a documented exception for `CLAUDE_CODE_OAUTH_TOKEN`; all other secrets use `type=mount`. Re-verify after Podman upgrades.
- **Bus-bound error sanitization:** transport and outbound paths use `SanitizedError` with
  `type(exc).__name__` only — never `str(exc)` on NATS-bus-bound fields. Enforced by the
  `str_exc_bus_bound` quality gate (`tools/check_str_exc_bus_bound.sh`).

---

## Open questions / known gaps

- Hub still runs `WebIntelScraper` subprocess — admitted as debt; target HTTP provider ([scrape-placement.md](scrape-placement.md)).
- worker→provider rename incomplete: the heartbeat-consuming registry is still `WorkerRegistry` (`src/factory/nats/worker_registry.py`); satellite-flavored naming exists only in newer code and docs.
- Uniform 5-layer dispatcher: design locked, rollout incremental; the shared tool protocol layer (in-process/remote wrappers, manifest) is not yet built in `packages/roxabi-contracts/`.
- Voice and image subjects still use pre-infix prefixes (`factory.voice.tts.request`, `factory.image.generate.request`); only socialmedia carries the tool infix on the wire (`factory.tool.socialmedia.>`).
- ADR-007: the non-streaming path (`cli_pool_send.py`) still warns-and-ignores model-config mismatch; migration gated on model-selector work.
- ADR-005 / #112: CliPool subprocess isolation (one subprocess per scope) and memory-namespace isolation per scope are not implemented; only hub-layer pool isolation is complete.
- ADR-031: `register_session_command` (legacy) and `@register` (processor registry) coexist without a migration deadline; new commands should use `@register`.
- ADR-019: `MessageManager` is shared and keyed to the first sorted agent's `i18n_language`; per-agent catalogs are an open refinement.

---

## See also

- Tool decision archive: `artifacts/analyses/493-tool-system-articulation-analysis.mdx` (D1-D16) + `493-tool-domain-nature-consolidation.mdx` (taxonomy B + runtime vocab).
- Adapters (inbound/outbound platform edges) → `adapters.md`
- Wire schemas (roxabi-nats, roxabi-contracts) → `contracts.md`
- Messaging planes & routing → `messaging.md`
- Deployment & Quadlet → `deployment.md`
- Security & credentials → `security-routing.md` (ADR-071 cross-listed)
- LLM provider layer (`LlmProvider` port, driver stack) → `llm-streaming.md`

---

## ADR archive

| ADR | Title | Status |
|-----|-------|--------|
| 004 | CliPool cwd resolution & AgentBase annotation | Superseded — archived (`adr/archive/`) |
| 005 | Wildcard binding & per-scope pools | Superseded (amended by #125) — archived (`adr/archive/`) |
| 006 | Hub run-loop error reply | Superseded — archived (`adr/archive/`) |
| 007 | Model-config mismatch | Superseded — archived (`adr/archive/`) |
| 010 | External tool pattern (Install–Wrap–Declare) | Amended — active |
| 019 | Multi-bot startup resource sharing | Superseded — archived 2026-07-02 (rationale dead; invariant lives in Key invariants above) |
| 026 | Pool callback wiring — eager at pool resolution | Superseded — archived (`adr/archive/`) |
| 030 | Tool-provider protocol for session commands | Superseded — archived (`adr/archive/`) |
| 031 | ProcessorRegistry & concurrent outbound dispatch | Superseded in part (#372) — archived (`adr/archive/`) |
| 038 | Health monitoring layer boundaries | Superseded — archived (`adr/archive/`) |
| 061 | Importlinter independence contract — port-import fix | Superseded — archived 2026-07-02 (plan landed minus SessionToolsProtocol, tracked as debt) |
| 071 | CliPool Claude OAuth token mechanism | Accepted — amended 2026-07-04 (lyra→factory unit/secret names) |
