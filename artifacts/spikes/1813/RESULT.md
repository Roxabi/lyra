# Spike #1813 — omp_rpc session continuity / resume / concurrency

**Date:** 2026-06-13 · **Env:** `factory-omp` container on M₁ (binary `/opt/omp/omp`,
sha `b877091…` = pinned), prod `models.yml` (`grok-4-fast` via LiteLLM proxy `:18091`).
**Result:** 🟢 **4/4 PASS** (`omp_session_spike.py`).

| Probe | Result | Evidence |
|-------|--------|----------|
| P0 wiring | PASS | `session_id=019ec286-…` + `session_file=/tmp/omp_spike_1813/…_019ec286-….jsonl` |
| P1 continue | PASS | turn2 (same client) recalled `ZORGLUB-7` |
| P2 resume | PASS | client A → `session_file`; client B (**new process**) `switch_session(sfile)` recalled `QUETZAL-42`; **`sid_a == sid_b`** |
| P3 concurrency | PASS | 2 clients (2 omp subprocesses) parallel → `session_a=ALPHA-1`, `session_b=BRAVO-2`, no bleed |

## Findings → spec/plan impact

1. **omp session "token" = `session_file`** (a `.jsonl` path), the omp analogue of clipool
   `cli_session_id`. Resume = `switch_session(session_file)`; it preserves the `session_id`.
   → **χ#2 RESOLVED.** Hub persists `session_file` keyed by conversation `session_id`
   (same pattern as `pool_sessions.cli_session_id`); on resume, driver passes it → worker
   `switch_session`.

2. **Concurrency works via multiple `RpcClient` instances** (one omp subprocess each) — omp
   CAN mirror clipool's `CliPool` (per-agent parallel turns). It is NOT inherently
   single-replica-serialized. The current `RpcBridge` uses ONE shared client +
   `no_session=True` + one `new_session()` → must be reworked either way.
   → **design decision:** serialize-first (1 client + `switch_session`/turn) vs omp-pool
   (N warm clients, `switch_session` per turn → N parallel turns, any conversation).

3. **Durability:** `session_file` is a real on-disk `.jsonl`. The spike used a tmpfs
   `session_dir` → resume works **within a run** but files vanish on restart. For
   resume-across-worker-restart (clipool parity), `session_dir` must be a **persistent
   volume**, not the ephemeral `PI_CODING_AGENT_DIR` tmpfs.
   → **χ#3 stands:** persist `session_dir` on a named volume.

## API confirmed (omp_rpc.RpcClient)

- `new_session(parent_session=None) -> CancellationResult`
- `switch_session(session_path: str|Path) -> CancellationResult`  ← resume by file path
- `get_state() -> SessionState` with `.session_id` and `.session_file`
- `prompt_and_wait(message, *, timeout=None)`  ← per-call timeout, no fixed cap
- constructor: `session_dir`, `provider_session_id`, `no_session`, `custom_tools`

Throwaway: `omp_session_spike.py` (artifact, not shipped). Script + session files removed
from the container after the run.
