# Spike #1807 — omp-rpc PoC RUNBOOK

## What this is

Throwaway spike. Validates whether `omp-rpc` (oh-my-pi Python RPC client) can
replace `claude -p` inside NATS job workers. Four probes:

| Probe | What it tests |
|-------|---------------|
| `text_turn` | New session → text prompt → `text_delta` events → `TextLlmEvent` shape |
| `tool_turn` | Host-tool registration via `custom_tools=` → `ToolExecutionStartEvent` → `ToolUseLlmEvent` shape |
| `steer_probe` | `steer()` from background thread mid-turn → no crash → `steer_queued` acknowledgement |
| `abort` | `abort()` from background thread → turn cancelled without exception in caller |

## Box choice

| Box | Can run | Notes |
|-----|---------|-------|
| M2 (roxabitower) | YES (preferred) | `omp` binary + Bun 1.3.14 + LiteLLM reachable at `localhost:4000` (llmcli-nats-worker) |
| M1 (roxabituwer) | YES | LiteLLM is local; `omp` must be installed |
| Laptop | MAYBE | Needs `omp` installed + Tailnet route to `roxabituwer:4000` |

**Important**: `omp` is NOT a compiled binary. It is a bash shim:
```bash
exec bun "$PI_ROOT/packages/coding-agent/src/cli.ts" "$@"
```
Requires: Bun 1.3.14 + full oh-my-pi source tree + `bun install` (hoisted node_modules).
Image footprint: ~450–700 MB compressed. Bun binary alone: ~70 MB.

## Install

### 1. omp itself (if not already present)

```bash
# Verify oh-my-pi source is at:
ls /home/mickael/projects/external_repos/oh-my-pi/

# If missing: clone it first
# git clone https://github.com/oh-my-pi/oh-my-pi /home/mickael/projects/external_repos/oh-my-pi

# Install bun (if not present):
curl -fsSL https://bun.sh/install | bash   # pins to latest; pin 1.3.14 for prod

# Install omp deps:
cd /home/mickael/projects/external_repos/oh-my-pi
bun install

# Verify omp is callable:
bun packages/coding-agent/src/cli.ts --version
# or if omp shim is on PATH:
omp --version
```

### 2. Python omp-rpc client

```bash
# From the worktree (DO NOT uv sync — it churns shared uv.lock):
uv pip install -e /home/mickael/projects/external_repos/oh-my-pi/python/omp-rpc
```

## Env

| Variable | Default | Notes |
|----------|---------|-------|
| `OMP_PROVIDER` | `litellm` | Use `anthropic` to bypass LiteLLM |
| `OMP_MODEL` | `claude-opus-4-6` | Any model name discoverable from the provider |
| `OMP_BIN` | `omp` | Full path to omp if not on PATH |
| `LITELLM_API_KEY` | (unset) | Optional — LiteLLM `allowUnauthenticated: true` |
| `OMP_LITELLM_BASE_URL` | `http://localhost:4000/v1` | **Informational only** — no env-var override path exists into omp's `LiteLLMModelManagerConfig.baseUrl`. See note below. |

> **SUPERSEDED 2026-06-10 (#1811):** the "hard-coded baseUrl" claim below is wrong —
> `localhost:4000/v1` is only a default (`config?.baseUrl ?? …`). omp's
> `models.yml` (`providers.litellm.baseUrl`, config dir relocatable via
> `PI_CODING_AGENT_DIR`) overrides it with priority 1. See `deploy/omp/README.md`.
> The env-var part stays true: no `LITELLM_BASE_URL` env var exists.

**LITELLM_BASE_URL gap**: The LiteLLM `baseUrl` is hard-coded in
`packages/ai/src/provider-models/openai-compat.ts:2191` at TS-constructor level.
No env var (`LITELLM_BASE_URL` does not exist in the codebase). To override from M2
pointing at M1's LiteLLM (`roxabituwer:4000`), options are:
1. ssh port-forward: `ssh -L 4000:localhost:4000 roxabituwer` → omp sees `localhost:4000`.
2. Custom `--command` launcher (pass a modified bun invocation with a config shim).
3. Run on M1 directly where `localhost:4000` resolves correctly.

## Run

```bash
cd /home/mickael/projects/roxabi-factory/.claude/worktrees/1807-runtime-eval/artifacts/spikes/1807

# All probes:
python omp_rpc_poc.py --all

# Individual:
python omp_rpc_poc.py --text
python omp_rpc_poc.py --tool
python omp_rpc_poc.py --steer
python omp_rpc_poc.py --abort
```

Expected startup time: 2–5 s (omp `ready` handshake + session init + model discovery).

## What GREEN looks like

```
Config: provider='litellm' model='claude-opus-4-6' bin='omp'
  NOTE: OMP_LITELLM_BASE_URL='http://localhost:4000/v1' is informational only — ...

PROBE             RESULT    DETAILS
--------------------------------------------------------------------------------
text_turn         PASS      text='PONG' session_id='<uuid>'
tool_turn         PASS      tool_calls=['echo_host'] session_id='<uuid>'
steer_probe       PASS      steer_sent=True session_id='<uuid>'
abort             PASS      abort_error=[] session_id='<uuid>'
--------------------------------------------------------------------------------
OVERALL           PASS
```

- `text_turn PASS`: non-empty text returned + "PONG" in reply → confirms LiteLLM route works.
- `tool_turn PASS`: `ToolExecutionStartEvent` received with `tool_name='echo_host'` → confirms
  host-tool round-trip (Python execute callback invoked → result returned to model).
- `steer_probe PASS`: `steer()` called from thread while `prompt_and_wait()` blocking → no
  exception raised. Effect on output is non-deterministic (depends on turn phase).
- `abort PASS`: `abort()` from thread → turn terminates; no exception propagated to `_abort_thread`.

## Teardown

No persistent state from this PoC. Sessions are in-memory; omp subprocess exits when
`RpcClient` context manager exits.

```bash
# Clean up any temp files if created:
rm -rf /tmp/omp_spike_1807
```

## Known gaps / risks for live run

1. **No baseUrl env override** *(superseded 2026-06-10 — `models.yml` overrides baseUrl, see `deploy/omp/README.md`)*: To route to M1 LiteLLM from M2, ssh tunnel or run on M1.
2. **omp image footprint**: ~450–700 MB compressed for full pi-runtime image. Quadlet integration
   is a heavyweight commitment — not a single-binary drop-in.
3. **steer timing non-determinism**: `steer(interruptMode="immediate")` applies between tool
   calls only. For turns with no tool calls, steer may be queued until the next turn.
4. **Model discovery at runtime**: LiteLLM model list fetched via `/v1/models` at session start.
   If the proxy is down or returns empty, omp may fail to resolve the model.
5. **AgentEndEvent does not carry session_id**: `ResultLlmEvent.session_id` is populated via
   a separate `get_state()` call made before the turn. If the turn crashes before `on_agent_end`,
   the `ResultLlmEvent` will be `None` in the collector.
6. **`require_assistant_text()` vs `on_message_update` delta aggregation**: Both paths emit text.
   The PoC uses `require_assistant_text()` as fallback but `on_message_update` delta chunks as
   primary. In production, choose one path only to avoid double-counting.
