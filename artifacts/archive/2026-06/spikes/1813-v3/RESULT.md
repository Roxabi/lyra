# Spike #1813 V3 — omp session/process decoupling (Model A vs B)

Run: 2026-06-15, inside `factory-omp` on M₁, `OMP_MODEL=grok-4-fast`, via
`podman exec -i factory-omp /app/.venv/bin/python -` (omp_rpc + `/opt/omp/omp` + LiteLLM proxy).

## Question

Before planning V3 concurrency: is the right model **A** (warm omp process pinned per
conversation, current V2) or **B** (flat pool of interchangeable workers, durable session
resumed per turn)? The fork hinges on (a) does omp support durable, process-independent
sessions, and (b) what does a resume cost vs the unavoidable LLM call.

## Findings

### omp_rpc API (verified, not assumed)
- `RpcClient(..., no_session: bool = False, session_dir=None, request_timeout=30.0, ...)`.
- `no_session=True`  → session **in-memory only**: `get_state().session_file is None`
  even after a turn; no `.jsonl` on disk. **This is what OmpPool + `_rpc_bridge` hardcode.**
- `no_session=False` → each session persists `PI_CODING_AGENT_DIR/sessions/--app--/<ts>_<id>.jsonl`;
  `get_state().session_file` is that path.
- `switch_session(session_path: str|Path)` resumes a `.jsonl` on **any** process.
- `new_session(parent_session=None)` starts a fresh session (new file).
- `request_timeout=30s` default → `grok-4` (full reasoning) times out; `grok-4-fast` fits.
  `model=None` makes omp default to the **first** `models.yml` entry (`grok-4`) → 30s timeout.

### Probe results (no_session=False, grok-4-fast)
| Probe | Result |
|---|---|
| A — isolation + live re-switch A→B→A | PASS — `isolated=True`, `recalled_after_live_switch=True`, **switch=4ms** |
| B — cross-PROCESS resume by file | PASS — fresh process recalled writer's codeword; `cold_start=724ms`, **switch=16ms** |
| C — timing | `start=716ms`, `prompt_LLM=2315ms`, **switch_session=4ms** |

### Interpretation
- A session is **fully decoupled** from its process. Resume ≈ free (4–16ms) vs ~2300ms LLM.
- Warm-pinning (Model A) saves ~4ms/turn = nothing — inference is remote (llmCLI), so an omp
  process holds **no GPU/model warmth**, only conversation history that a 4ms switch reloads.
- Cold process start (~720ms) is one-time (pool growth), not per-turn.

## Latent gap in V2 (discovered here)
V2's OmpPool/`_rpc_bridge` use `no_session=True` → `session_file` is always `None` → the V2
"native-session memory via `provider_session_id` = `.jsonl` path" mechanism is a **no-op**.
V2 memory works **only** via warm in-process retention (accidental Model A). On LRU eviction
(cap=4) or worker restart the session is **lost** — no file to resume. Not exercised because
omp isn't a default backend.

## Decision → Model B

Flat pool of M interchangeable omp workers (M sized to **RAM**, not a conversation count;
GPU is out of scope — owned by llmCLI). Sessions durable on disk (`no_session=False`),
identified by `session_file`, persisted in `pool_sessions` keyed by Lyra `session_id`.
Per turn: check out any free worker → `switch_session(file)` (4ms) → run → release.

Flipping `no_session=False` simultaneously: (a) makes V2's resume actually work, (b) makes
memory survive eviction/restart, (c) enables Model B.

### Costs to design for (feed the plan)
1. **Durable sessions = content at rest + growth.** `PI_CODING_AGENT_DIR` is currently an
   ephemeral tmpfs (1777) chosen for no-secret-at-rest/no-growth. Durable sessions need a
   persistent volume for `sessions/` + a retention TTL. Devops scope.
2. **`request_timeout` / default model.** Worker `model=None` → grok-4 → 30s timeout risk.
   Pin `grok-4-fast` (or raise `request_timeout`). Prod latent issue — worth a follow-up.
3. **Reworks V2's OmpPool** (pin-per-conversation → flat worker pool). Acceptable: V2's
   resume was a no-op, so this is a fix, not churn.

## Repro
```
ssh roxabituwer 'podman exec -i factory-omp env OMP_BIN=/opt/omp/omp OMP_MODEL=grok-4-fast \
  /app/.venv/bin/python -' < omp_session_switch_probe.py
```
Helpers: `omp_introspect.py` (state/turn attrs), `omp_api_dump.py` (signatures + persistence).
