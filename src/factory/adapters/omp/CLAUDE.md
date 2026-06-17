# src/factory/adapters/omp/ — OmpWorker NATS Backend

## Purpose

NATS worker adapter that dispatches jobs from `FACTORY_JOBS` (`factory.jobs.omp` queue
group `omp-workers`) to the omp coding agent (oh-my-pi runtime) via `omp_rpc.RpcClient`,
then publishes per-job lifecycle events back to the bus.

## Layer invariants

- **Digest gate** — `RpcBridge.__init__` calls `_verify_digest()` before constructing any
  omp_rpc RpcClient; raises `DigestMismatchError` if the sha256 of `/opt/omp/omp` does not match
  `_PINNED_SHA256 = "b877091c91ebdc8c8d907c4b62681895cd3ae049815858aea7b69ac1d53b7c7b"`.
  Both `_OMP_BIN` and `_PINNED_SHA256` are image-build constants in `_rpc_bridge.py` —
  never read from env. Carrier bump must land with the pin update.

- **`tool_input` never bus-published** — `_on_tool_execution_start` receives the omp_rpc
  event but explicitly omits `tool_input` from the NATS `JobProgress` payload.
  `tool_input` may contain credentials or file fragments (ADR-073).

- **`_result_sent` double-publish guard** — boolean flag on `RpcBridge`; checked in both
  `run()` (success path) and `publish_error` (error path). Only the first caller publishes a
  `JobResult`; the second is a no-op. Guard is per-job-invocation — reset at the start of each
  `run()` call, which is what makes sequential jobs on one bridge safe. Do not remove the reset.
  `run()` is the sole success publisher (race-free, after `await asyncio.to_thread(prompt_and_wait)`
  returns on the event loop). `_on_agent_end` is store-only: it stores the event in
  `_last_agent_end_event` so `run()` can derive fallback text from `event.messages` if needed.

- **ADR-073 SanitizedError discipline** — `_classify_exception` uses `type(exc).__name__`
  only; never `str(exc)`, `f"{exc}"`, or `repr(exc)` in bus-bound fields.

- **Config path** — `PI_CODING_AGENT_DIR` points to omp's agent dir
  (`/home/factory/.config/omp-pi`). It is a **persistent named volume** (`factory-omp-sessions`,
  #1897) mounted `rw`: omp writes `agent.db` + `models.db` (SQLite) there at boot, so a
  whole-dir `:ro` mount EACCESes the DB open (#1879). Since #1813 (Model B, `no_session=False`)
  it ALSO holds durable `.jsonl` session files under `sessions/`, resumed per turn via
  `switch_session` — so the dir MUST persist across restarts (this volume replaces the prior
  tmpfs; `.jsonl` growth is bounded by a retention policy — devops, tracked). `models.db`
  recompiles from `models.yml` each boot. The
  repo's `models.yml` is layered **read-only on top** (SSoT). The LiteLLM base URL override
  lives in `models.yml` under `providers.litellm.baseUrl` — it is NOT an env var.

- **Writable runtime FS under `ReadOnly=true`** — omp also writes `$HOME/.omp` at boot
  (file-log transport + native modules unpacked & dlopen'd under `natives/`). It is a
  tmpfs at **`mode=1777`**: a root-owned `0755` tmpfs EACCESes for uid 1500, and `noexec`
  is NOT viable (the `natives/` are dlopen'd). Ephemeral is correct here — `$HOME/.omp` holds
  only unpacked native modules + the file-log, never session state (sessions live durably in
  `PI_CODING_AGENT_DIR`, above).

- **Image** — `factory-omp.container` runs `ghcr.io/roxabi/factory:staging` with
  `/opt/omp/omp` baked in via `COPY --from factory-omp-base`. The carrier (`factory-omp-base`)
  is FROM scratch and can never exec `factory adapter omp` (#1867).

## NATS subjects

| Subject | Direction |
|---------|-----------|
| `factory.jobs.omp` | inbound — job dispatch (queue group `omp-workers`) |
| `factory.job.<job_id>.steer` | inbound — hub steering prompt (subscribe) |
| `factory.job.<job_id>.progress` | outbound — progress events |
| `factory.job.<job_id>.result` | outbound — final result |

Subjects resolved via `roxabi_contracts.jobs.subjects`.

## Secrets required

`factory-nats-omp` (NATS credentials) · `factory-litellm-key` (LiteLLM API key)
