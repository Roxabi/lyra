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
  `_on_agent_end` and `publish_error`. Only the first caller publishes a `JobResult`; the
  second is a no-op. Guard is per-job-invocation — reset at the start of each `run()` call,
  which is what makes sequential jobs on one bridge safe. Do not remove the reset.

- **ADR-073 SanitizedError discipline** — `_classify_exception` uses `type(exc).__name__`
  only; never `str(exc)`, `f"{exc}"`, or `repr(exc)` in bus-bound fields.

- **Config path** — `PI_CODING_AGENT_DIR` env var points to `deploy/omp/` (mounted as
  `/home/factory/.config/omp-pi:ro` in the container). omp reads `models.yml` from there.
  The LiteLLM base URL override lives in `models.yml` under `providers.litellm.baseUrl` —
  it is NOT an env var.

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
