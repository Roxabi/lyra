---
title: Tool Architecture — taxonomy & runtime vocabulary
description: Canonical model for the Roxabi Factory tool layer — the 5-layer tool taxonomy, the workerEngine/harness/worker/provider runtime vocabulary, the tool-vs-plomberie domain-nature split, and the two discriminators (tool-surface≠tool-nature, provider⊋satellite).
---

# Tool Architecture — taxonomy & runtime vocabulary

> Status: LIVING — canonical conceptual model for the tool / worker / harness layer.
> Last updated: 2026-06-03.
> Distilled from (decision archive): `artifacts/analyses/493-tool-system-articulation-analysis.mdx`
> + `493-tool-domain-nature-consolidation.mdx`.
> Implementation reference: `workers-tooling.md`. Wire schemas: `contracts.md`.

## Scope

What a tool / worker / harness / provider **is**, the two taxonomies that organize them, and the
rules that keep them distinct. **Excludes** implementation internals (CliPool, registries →
`workers-tooling.md`), NATS wire schemas (`contracts.md`), deployment (`deployment.md`).

Status: design-locked (D1-D16 validated 2026-06-02); implementation **parked** pending #1670.

## 1. The layers

```
worker  (a compute instance)
  └─ runs ─▶ workerEngine  =  coded pipeline (deterministic workflow)
                │ calls, as steps:
                ├─▶ harness            pure agentic turn (LLM + in-turn tools) · no pipeline logic
                ├─▶ tools  ──backed by──▶ provider ──(if self-hosted)──▶ satellite
                └─▶ internal code
```

A **workerEngine** orchestrates a deterministic workflow; it *calls* the agentic primitive
(**harness**), **tools**, and **internal code** as steps. A **harness** runs one pure agentic turn
(the LLM decides; tools dispatched within the turn) with **no** workflow logic — it is a *callee*,
not a flavor, of the workerEngine. The two are **layered, not unified** (harness = #1490;
workerEngine ≈ #1044 code-worker runtime).

## 2. Runtime vocabulary (canonical glossary)

| Term | Nature | Definition |
|---|---|---|
| **workerEngine** | plomberie · runtime | coded pipeline (deterministic); orchestrates harness + tools + code |
| **harness** | plomberie · agentic | one pure agentic turn (LLM + in-turn tools); no pipeline logic; callee of workerEngine |
| **worker** | plomberie · compute | a deployed instance running a workerEngine; consumes tools |
| **tool** | **tool-nature** | a capability surface invoked by harness / workerEngine |
| **provider** | plomberie · backend | the **role** that backs ≥1 tool; transport-agnostic |
| **satellite** | plomberie · deployment | a provider deployed as a self-hosted NATS process with heartbeat (⊂ provider) |
| **backing service** | infra | the self-hosted app behind a provider (e.g. `roxabi-postiz`); not a tool/provider/satellite itself |

> "worker" was historically overloaded — 493 §5 used it for tool **providers**. Resolved:
> **worker = compute-on-engine** (consumer); the tool-backing things are **providers**.

## 3. Taxonomy A — the 5-layer tool model

One tool, five layers, **one uniform dispatcher** (one ACL gate, one result type, transport
varies — the LLM sees a built-in `bash` and a remote `image.generate` identically):

| Layer | Name | What | Transport |
|---|---|---|---|
| **L0a** | Built-in atomic | compiled in the engine, in-process | — |
| **L0b** | Remote atomic | a provider on the bus, discovered by heartbeat | NATS / stdio / http |
| **L1** | Macro | deterministic chain of primitives, no second LLM | in-proc or remote |
| **L2** | Skill | instruction-layer composite (SKILL.md) guiding the LLM | prompt injection |
| **L3** | Sub-agent | nested isolated agentic call, returns a summary | NATS harness |

## 4. Taxonomy B — domain-nature (NATS contract domains)

Wire domains split by **nature**:

| | Domains |
|---|---|
| **tool** (capability surface) | `voice` · `image` · +future (`postiz`, `xcli`, `vault`, `scrape`, `camoufox`) |
| **plomberie** (substrate / infra) | `cli` · `llm` · `jobs` · `gh` · `turns` · `blob` · `verify` · `audit` · `event` · `outbound` |

Tool-nature subjects carry a `tool.` infix → `factory.tool.<cap>.*`; the consumer side addresses
the whole tool plane as `factory.tool.>`. Plomberie keeps `factory.<domain>.*`. Package shape:
`roxabi-contracts/tool/{voice,image,…}` (wire schemas) vs `roxabi-contracts/tools/` (the *protocol*
layer — `InProcessTool` / `RemoteTool` / `ToolManifest`).

## 5. The two discriminators (the sharp rules)

**Rule 1 — tool-surface ≠ tool-nature.** A plomberie domain MAY *expose* a tool without *being*
tool-nature. `gh` exposes a built-in `gh.list_prs`; `llm` exposes `llm.generate` — both stay
plomberie. Exposing a tool does not promote the wire domain.

**Rule 2 — provider ⊋ satellite.** A provider is a *satellite* only when it is a self-hosted
process we run on NATS with a heartbeat. Otherwise it is a non-satellite provider:

| Backing | Provider shape | Heartbeat |
|---|---|---|
| self-hosted process we run (imageCLI, voiceCLI, Postiz-via-adapter) | NATS satellite adapter | ✅ |
| cloud API we don't host (X, GitHub) · one-shot stdio CLI | in-proc HTTP / stdio | ✗ |

Corollary — **three layers, do not conflate**: a **backing service** (e.g. `roxabi-postiz`, a
self-hosted Postiz fork: web+DB+Redis, HTTP) is infra like Postgres. **A running container ≠ a
heartbeat** — the heartbeat comes from *our* NATS adapter (the provider), not the HTTP app.
backing service ≠ provider ≠ tool.

## Key invariants

- A worker is **not** a tool; it consumes tools and **runs on** a workerEngine.
- The harness is **pure agentic** (no workflow logic) and is **called by** the workerEngine — layered, never unified.
- The tool dispatcher is **uniform**: one ACL gate, one `ToolResult`, transport-agnostic. No per-kind branched pipeline.
- tool-surface ≠ tool-nature: a plomberie domain exposing a tool stays plomberie.
- provider ⊋ satellite: self-hosted backing → satellite (heartbeat); cloud / one-shot → non-satellite. A running backing container is not a heartbeat.
- "worker" = compute-on-engine (consumer). Tool-backing things are "providers"; `satellite` = the NATS-deployed kind.

## Status & sequencing

- Design **locked** (D1-D16 validated 2026-06-02); implementation **parked** pending #1670 (`lyra.*→factory.*` subject migration).
- **Rides #1670** (same `roxabi-contracts` package): `tool/` reparent + `factory.tool.*` infix.
- **Separable from #1670**: `worker→provider` rename (`WorkerRegistry→SatelliteRegistry`, `hosts.toml` roles).
- **Open** — Postiz provider = satellite adapter, not bash CLI (revisits 493 D11): tracked #1713.

## See also

- Decision archive: `artifacts/analyses/493-tool-system-articulation-analysis.mdx` (D1-D16, reviewers, tracks) + `493-tool-domain-nature-consolidation.mdx` (taxonomy B + runtime vocab).
- Implementation: `workers-tooling.md` (CliPool, satellite clients, registries, ADR-010/030).
- Wire schemas: `contracts.md` (roxabi-nats, roxabi-contracts).
- Epics: #493 (tools capability layer) · #1490 (harness) · #1044 (worker fleet) · #1713 (Postiz satellite).
