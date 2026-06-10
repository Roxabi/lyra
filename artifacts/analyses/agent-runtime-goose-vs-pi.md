---
title: Agent-runtime evaluation — Goose vs Pi-family for the lyra-harness NATS worker
status: RATIFIED 2026-06-10 (spike #1807 🟢 GREEN) — 4 claims corrected post-spike, see banner
date: 2026-06-09
source: multi-agent workflow (goose-vs-pi-nats-fit, 2 passes, ~350k tokens) + hand-verification
relates: "#1490 (harness epic), #1807 (spike), #1792 (Shape D), #1799 (steer), #1044 (worker fleet), #493 (ToolHandler)"
---

# Agent-runtime evaluation — Goose vs Pi-family

> Which OSS agent runtime should run *inside* the `lyra-harness` NATS worker (#1490),
> replacing the `claude -p`/clipool agent loop? Decision direction: **Pi-family (oh-my-pi
> runtime + earendil/pi anchor), NOT Goose.** Final commit gated on spike **#1807**.

> [!WARNING]
> **Ratified 2026-06-10 — spike #1807 🟢 GREEN (`1807-spike-result.md`), decision recorded on
> [#1490](https://github.com/Roxabi/roxabi-factory/issues/1490#issuecomment-4666813172).
> Four claims below are superseded by spike-verified facts — read these corrections before citing:**
>
> 1. **Runtime is *pluggable*, clipool is NOT retired** — `omp_rpc` joins clipool/`claude -p` as a
>    per-agent/per-job backend (§8 "replaces the custom loop" / "phased rollout" is stale → #1813).
> 2. **Footprint: prebuilt `omp-linux-x64` standalone binary ~175 MB; Bun never enters the image**
>    (§7 C2 "~90 MB Bun runtime, Bun pinned" is stale on both axes → #1810).
> 3. **`LITELLM_BASE_URL` env override does not exist** — baseUrl is hard-coded
>    `http://localhost:4000/v1`, TS-constructor only (§6 "zero code" provider line is stale → #1811).
> 4. **"Bun footprint" spike gate (§8): resolved moot** by the prebuilt binary — no Bun-in-image concern.

## 1. Why this evaluation

- **Cost cliff.** On **2026-06-15** Anthropic splits interactive vs programmatic billing;
  `claude -p` / Agent-SDK headless usage (which `clipool` wraps) gets ~5–10× more expensive
  after a small credit pool (`docs/research-claude-p-alternatives-2026-06-08.md`). We need an
  **OSS, self-hostable, provider-agnostic** runtime routed through the existing **M1 LiteLLM
  proxy** (llmcli: cloud Kimi/Claude/DeepSeek + local M2 GPU via vLLM).
- **Runtime swappability is already a stated epic goal.** #1490 locks the v1 runtime as a
  *"custom thin loop (~200 lines Python)"* precisely so it can be swapped for an external
  runtime later. This eval asks: should we skip the custom loop and adopt an OSS runtime now?
- **The job model wants capabilities these runtimes may already have.** Shape D steer (#1799),
  sub-jobs/`composite_depth` (#1798), and the tools registry (#493) are all things `claude -p`
  cannot do — so we'd be building them. A runtime that ships them natively shrinks the build.

## 2. The integration contract (what a candidate must satisfy)

A candidate becomes the agent loop inside a **Python `NatsAdapterBase` worker** that:

| # | Requirement | Maps to |
|---|---|---|
| 1 | Headless turn with a **structured streaming event protocol surfacing tool-use** | `TextLlmEvent` / `ToolUseLlmEvent` / `ResultLlmEvent` (clipool contract) |
| 2 | **Provider-agnostic** incl. arbitrary OpenAI-compatible base_url | M1 LiteLLM proxy (cost driver) |
| 3 | **Stable programmatic API** (not an internal/unstable surface) | worker durability |
| 4 | Session persistence + resume | `resume_and_reset(session_id)` control op |
| 5 | **Mid-run steer / message injection** | `factory.job.<id>.steer` (Shape D #1799) |
| 6 | MCP / custom tools mapping onto `ToolHandler` | tools layer #493 |
| 7 | Sub-agent → sub-job decomposition | `parent_job_id` / `composite_depth ≤ 3` (#1798) |
| 8 | cwd + permission/approval sandboxing | `switch_cwd`, E-stop |
| 9 | OSS license + maintenance health | risk |

Reference worker: `src/factory/adapters/clipool/clipool_worker.py`. Target taxonomy:
`factory.jobs.<name>` (dispatch) → `factory.job.<id>.{progress,result,opened,closed,steer}`
(`docs/architecture/job-model.md`, the live SSoT).

## 3. Candidates & lineage

| Repo | What it is | Lang | Note |
|---|---|---|---|
| `aaif-goose/goose` | Block/AAIF agent (ex `block/goose`) | Rust | Apache-2.0, institutional |
| `earendil-works/pi` | Canonical "Pi" (Mario Zechner) | TS/Node | MIT, v0.79.0 — **stable upstream** |
| `badlogic/pi-mono` | Author's working mirror of the same code | TS/Node | v0.76.0 — redundant with earendil/pi |
| `can1357/oh-my-pi` | **Fork-superset** of pi-mono (Can Boluk) | TS+Bun+Rust | MIT, v15.10.8 — daily churn |

**oh-my-pi is not a plugin layer — it is a diverged superset fork.** It adds (vs base Pi):
named LiteLLM provider, MCP, 32 tools, LSP/DAP, `task` sub-agent tool, `mnemopi` memory,
`pi-iso` Rust sandbox, approval modes, **and a `python/omp-rpc` typed Python client**. So the
user framing *"Pi + Pi plugins"* resolves to **earendil/pi (stable spec anchor) + oh-my-pi
(integration target)**.

## 4. Scorecard (verified, file:line cited in §5)

Dimension scores 0–5; overall_fit is the weighted total (gates: streaming-fidelity & API-stability).

| Dimension (weight) | Goose | earendil/pi | **oh-my-pi** |
|---|---|---|---|
| headless | 4 | 5 | 5 |
| **streaming fidelity** (gate) | **3** (reconstructable) | 5 (first-class) | **5** (first-class) |
| **API stability** (gate) | **1** (`_goose/unstable/*`) | 4 (documented RPC) | **4** (documented RPC) |
| provider / LiteLLM | 5 (turnkey) | 5 (code-only) | 5 (turnkey named) |
| sessions | 4 | 5 | 5 |
| steer | 1 (cancel-only) | 5 (`steer()`) | 5 (`steer()`) |
| tools → #493 | 5 (MCP, adaptable) | 4 (clean) | 5 (clean + MCP) |
| sub-jobs | 3 | 1 | 4 (`task` tool) |
| sandbox | 3 | 2 | 4 (approval + pi-iso) |
| embeddability | 1 | 1 | 5 (lib + Python rpc) |
| maturity | 4 (Block) | 3 (solo MIT) | 4 (solo fork, churn) |
| **overall_fit** | **36** | **52** | **90** |

## 5. Decisive findings (with evidence)

### Goose is eliminated on the two gates
- **Coarse event stream.** `AgentEvent = {Message, McpNotification, HistoryReplaced}`
  (`crates/goose/src/agents/agent.rs:252`); tool calls are `MessageContent::ToolRequest/ToolResponse`
  buried inside `Message.content` (`message.rs:264`). Reproducing `ToolUseLlmEvent` requires
  fragile deep-pattern-matching — the exact anti-pattern clipool's typed stream avoids.
- **Unstable programmatic surface.** The Python uniffi SDK is a ping→pong scaffold
  (`goose-sdk/src/lib.rs:11-13`, `bindings.rs`). The only real headless path is `goose acp` over
  stdio JSON-RPC, where **every** custom method is `_goose/unstable/*`-prefixed
  (`goose-sdk-types/src/custom_requests.rs`, `custom_notifications.rs:9`) — no stability guarantee.
- **Steer = cancel-only.** `interrupt_agent` → `cancel_session` (CancellationToken)
  (`orchestrator.rs:542`); no mid-turn message injection.
- Counterweight (real but insufficient): Apache-2.0, Block/AAIF backing, mature MCP ecosystem,
  turnkey LiteLLM provider (`crates/goose/src/providers/litellm.rs`), sub-agent orchestrator.

### Pi-family wins on event fidelity + native job-model primitives
- **First-class tool events.** `tool_execution_start/update/end` are discrete typed events
  (oh-my-pi `packages/agent/src/types.ts:494-515`; earendil/pi `packages/agent/src/types.ts`) →
  1:1 map to `ToolUseLlmEvent` with no reconstruction.
- **Native steer().** `session.steer(text, images?)` pushes to the agent `#steeringQueue`
  (oh-my-pi `packages/agent/src/agent.ts:708-712`; RPC `steer` command in `docs/rpc.md`). This is
  Shape D's "missing" primitive — **we bridge `factory.job.<id>.steer` → `session.steer()`, we do
  not build the loop.**
- **Turnkey LiteLLM (oh-my-pi).** Named first-class `litellmProvider`
  (`packages/ai/src/registry/litellm.ts:40-48`), default baseUrl `http://localhost:4000/v1`
  (`packages/ai/src/provider-models/openai-compat.ts:2191`) = our llmcli exactly,
  auto-discovered via `LITELLM_API_KEY` (`allowUnauthenticated:true`). **Zero code.**
  (earendil/pi has *no* named litellm provider — needs a typed TS `Model<'openai-completions'>`
  struct; one file edit, not zero.)
- **Sub-jobs + tools + sandbox (oh-my-pi).** `task` sub-agent tool with `taskDepth`
  (`docs/sdk.md:281-290`) → `composite_depth`; MCP + `CustomToolFactory` (`docs/custom-tools.md`)
  → ToolHandler #493; approval modes (`docs/approval-mode.md`) + `pi-iso` Rust fs sandbox.
- **Linchpin — Python bridge (hand-verified).** `external_repos/oh-my-pi/python/omp-rpc/` is a
  typed Python client (MIT, `requires-python>=3.11`, Dev-Status **Alpha**): `RpcClient`,
  `prompt_and_wait`, `get_state`, typed per-event listeners, and **host-tool helpers that expose
  custom tools via JSON Schema** (= the ToolHandler bridge). The RPC wire protocol is documented
  in `docs/rpc.md` (16KB). → **the harness worker stays Python; no TypeScript worker is needed.**
  (This refutes the first adversarial pass, which missed `python/omp-rpc` + `docs/rpc.md`.)

## 6. Integration mapping (winner = oh-my-pi)

```
factory-omp Quadlet (Python container, Bun present for the omp subprocess)
  └─ OmpWorker(NatsAdapterBase, asyncio)
       └─ omp-rpc RpcClient ─drives─▶ `omp --mode rpc` (Bun subprocess, NDJSON stdio)

Subscribe  factory.jobs.omp            (queue group omp-workers)   ← JobRequest
Publish    factory.job.<id>.progress   (TextLlmEvent ← message_update; ToolUseLlmEvent ← tool_execution_*)
           factory.job.<id>.result     (ResultLlmEvent ← agent_end, done=True)
           factory.job.<id>.opened/closed   (register/clear factory-active-jobs KV)
Subscribe  factory.job.<id>.steer      (per-job) ─▶ RpcClient.steer(payload)

provider : env LITELLM_API_KEY + LITELLM_BASE_URL=http://roxabituwer:4000/v1   (zero code)
tools    : ToolHandler {name,description,input_schema,execute} ─▶ omp host-tool (JSON Schema)
sessions : SessionManager.inMemory() (Shape B) | persisted .jsonl + resume (Shape D)
sub-jobs : `task` tool spawn ─▶ child job opened w/ parent_job_id (composite_depth ≤ 3)
```

`concurrency_mode`: `queue`→Shape B (fresh session/req) · `steer`→Shape D (1 session/job_id, steer
sub live) · `parallel`→Shape B fan-out. The existing ToolHandler registry (#493) needs **zero
changes** — the omp worker is a new consumer.

## 7. Honest caveats (verified by adversarial pass)

| # | Caveat | Status |
|---|---|---|
| C1 | **Steer is between-tool-calls, not mid-tool.** `checkSteering()` fires after the current tool returns (oh-my-pi `agent-loop.ts:1249-1263,1522`; earendil/pi `agent-loop.ts:253`). Latency = remainder of the running tool. Urgent steer = `abort()`+resubmit (= our `resume_and_reset`). *No* runtime has true mid-tool interruption. | confirmed |
| C2 | **Bun enters the Quadlet stack.** ~90 MB non-Python runtime on M1 → dedicated `factory-omp-base` image, Bun pinned. | real |
| C3 | **omp-rpc is Alpha + co-evolves with a daily-churn solo fork.** Pin a git SHA; integration tests on every bump. | real |
| C4 | **Fork/maintenance risk** (Can Boluk, v15.x). earendil/pi is the stable fallback anchor. | real |

## 8. Decision

**Choose the Pi-family. Adopt oh-my-pi as the harness runtime; keep earendil/pi as the stable
spec anchor and RED-path fallback.** This supersedes #1490's "custom thin loop" *if* the spike is
GREEN.

**Gate → spike #1807** (timeboxed): validate `omp-rpc` lifecycle + event mapping + LiteLLM zero-config
+ steer latency + Bun footprint.
- **GREEN** → omp worker replaces the custom loop in #1490; phased rollout alongside clipool.
- **RED** → earendil/pi over plain NDJSON subprocess (build LiteLLM provider, lose MCP/task), or
  retain the custom thin loop.

## Appendix — method

Two-pass multi-agent workflow `goose-vs-pi-nats-fit`: parallel per-candidate deep-dives on a fixed
10-dimension rubric (file:line citations) → weighted synthesis → 3 adversarial verifiers
(steer-reality, provider-reality, recommendation). Both factual adversarial checks (steer mechanism,
LiteLLM provider) returned **confirmed**; the recommendation-refutation's load-bearing claim
("no Python bridge") was itself refuted by hand-inspection of `python/omp-rpc` + `docs/rpc.md`.
Clones: `external_repos/{goose,earendil-pi,oh-my-pi}` (+ `pi-mono`).
