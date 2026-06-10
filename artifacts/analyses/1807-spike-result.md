# Spike #1807 — Result: oh-my-pi (`omp_rpc`) as the lyra-harness runtime

**Date:** 2026-06-10 · **Box:** M₂ (roxabitower) · **Model:** `grok-4-fast` via M₂ LiteLLM proxy
**Companion docs:** `agent-runtime-goose-vs-pi.md` (eval), `1807-omp-rpc-groundwork.md` (API), `1807-groundwork-2-wire-provider-bun.md` (wire/provider/Bun), `../spikes/1807/` (PoC + runbook).

---

## Verdict: 🟢 GREEN

`omp_rpc` (oh-my-pi's typed Python client) drives a complete agent turn through our LiteLLM proxy. All four runtime primitives the job model needs — streamed text, host-tool (JSON-Schema) invocation, mid-run steer, abort — work end-to-end from a Python `NatsAdapterBase`-shaped driver. **No TypeScript worker required.**

→ Recommend adopting oh-my-pi/`omp_rpc` as the harness runtime and **superseding the #1490 "custom thin loop (~200 lines)" Runtime decision** (pending ratification).

---

## Live evidence — PoC `omp_rpc_poc.py --all`

```
PROBE             RESULT    DETAILS
--------------------------------------------------------------------------------
text_turn         PASS      text='PONG' session_id='019eafed-b73d-...'
tool_turn         PASS      tool_calls=['echo_host'] session_id='019eafed-c92d-...'
steer_probe       PASS      steer_sent=True session_id='019eafed-e814-...'
abort             PASS      abort_error=[] session_id='019eafee-0e38-...'
--------------------------------------------------------------------------------
OVERALL           PASS
```

| Probe | Proves | Job-model tie-in |
|---|---|---|
| `text_turn` | `on_message_update` text_delta → `TextLlmEvent`; `on_agent_end` + `get_state()` → `ResultLlmEvent{session_id}` | streamed `factory.job.<id>.progress` + `.result` |
| `tool_turn` | host-tool `echo_host` registered via JSON-Schema, invoked, round-tripped; `on_tool_execution_start` → `ToolUseLlmEvent{tool_name,tool_id,input}` | #493 ToolHandler bridge — **native** |
| `steer_probe` | `steer()` injected from a background thread mid-turn; lands between tool calls | Shape-D #1799 mid-run injection — primitive exists, we only bridge NATS→steer() |
| `abort` | `abort()` clean, client reusable | turn cancellation / `resume_and_reset` |

Event mapping (groundwork) is now **live-verified 1:1** — no fragile reconstruction (the anti-pattern that eliminated Goose).

---

## Footprint — REVISED ⚠️ (supersedes groundwork-2 §C)

Groundwork part-2 §C concluded "omp is **not** a compiled binary → ~450–700 MB source+Bun image." **The live run refutes this.** oh-my-pi ships a **prebuilt standalone ELF binary** via GitHub releases — the `ci-release-build-binaries.ts` output part-2 §E could not verify:

```
/tmp/omp_spike/omp-bin : ELF 64-bit LSB executable, x86-64 — 183,777,408 B (~175 MB)
                         omp-linux-x64 v15.10.8 (single dynamically-linked binary)
```

The workspace clone needs Bun + a compiled `.node` native addon (+ Rust to build it); M₂ has no Rust, so the prebuilt binary was used instead — and it Just Worked. Implication for `factory-omp-base`:

- Bake a **single ~175 MB binary** into the image (or bind-mount from host) — **no `bun install`, no `node_modules` tree, no Rust toolchain at runtime**.
- ~1/3 the earlier estimate, one clean artifact. Heavier than a Python wheel but a tractable Quadlet commitment.

---

## Confirmed caveats (carry into the ADR)

| Caveat | Status | Mitigation |
|---|---|---|
| `steer` = between-tool-calls, not mid-generation | confirmed live | urgent stop = `abort()`+resubmit (= `resume_and_reset` pattern) |
| subprocess-per-`RpcClient`, no daemon in `--mode rpc` | confirmed | matches per-slot pool model — pool = N `RpcClient` = N omp subprocesses |
| baseUrl hard-coded `http://localhost:4000/v1`, **no env override** | confirmed | factory proxy is on **:18091** → ran a TCP forward 4000→18091 for the spike. Prod: put LiteLLM on :4000 in the pod, sidecar-forward, or patch omp provider config |
| `LITELLM_API_KEY` required (proxy is **not** `allowUnauthenticated`) | confirmed | inherited via `{**os.environ, **self._env}` — no explicit pass needed; key = `LLMCLI_API_KEY` from `~/.roxabi/llmcli/env/proxy.env` |
| `switch_cwd` (no runtime RPC) + `resume_and_reset` (file-path, not id) | from groundwork | **moot under stateless Shape-B** (per-job omp process); only bites long-lived pooled Shape-D |

---

## Verified recipe (M₂, 2026-06-10)

1. Proxy: M₂ `llmcli` at `localhost:18091` (key `LLMCLI_API_KEY`). omp hard-codes `:4000` → TCP-forward `4000→18091`.
2. omp: prebuilt `omp-linux-x64` v15.10.8 binary + 1-line bash shim → `OMP_BIN=/tmp/omp_spike/omp`.
3. `omp_rpc`: isolated venv (`uv venv` + `uv pip install -e .../python/omp-rpc`) — never touches factory `uv.lock`.
4. Env: `OMP_PROVIDER=litellm OMP_MODEL=grok-4-fast LITELLM_API_KEY=$LLMCLI_API_KEY`.
5. PoC fix: `executable=OMP_BIN` on every `RpcClient(...)`.

Full steps → `../spikes/1807/RUNBOOK.md`.

---

## Decision gate (per #1807) — GREEN ⇒ recommended follow-ups

Out of scope here (this spike validated the linchpin only). If ratified under #1490:

1. **Production `OmpWorker`** (`factory.jobs.omp` dispatch → `factory.job.<id>.{progress,result,opened,closed,steer}`), Python `NatsAdapterBase` driving `omp --mode rpc`.
2. **`factory-omp-base`** image — bake the ~175 MB prebuilt binary; pin the omp release SHA + integration test on bump.
3. **LiteLLM on `:4000`** in the factory pod (or sidecar forward) — resolve the hard-coded baseUrl.
4. **Supersede** #1490 "custom thin loop" Runtime decision; retire clipool/`claude -p` after parity.
