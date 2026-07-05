# src/factory/infrastructure/jobs/ — FACTORY_JOBS Stream Provisioning

## Purpose

Idempotent JetStream `FACTORY_JOBS` WorkQueue stream provisioning and (Slice 3)
DLQ router for the factory job-dispatch bus (epic #1044; → `docs/architecture/messaging.md` § FACTORY_JOBS DLQ flow).

## Invariants

- **Hub sole-provisioner** — `ensure_jobs_stream` is called by `hub_standalone.py`
  before `announce_hub_ready`. Workers must NOT call it (→
  `docs/architecture/messaging.md` Key invariants).
- **Retention: WorkQueue** — exactly one consumer delivers each message; ack
  deletes it from the stream. Do NOT change to Limits (breaks single-delivery
  guarantee).
- **Explicit subject enumeration** — `factory.jobs.omp` is intentionally excluded.
  omp lane uses a core-NATS queue group (`factory.jobs.omp`); binding a WorkQueue
  consumer on this stream with no live consumer would silently accumulate messages
  to the `max_msgs` limit. See `docs/architecture/messaging.md` § FACTORY_JOBS DLQ flow.
- **`factory.jobs.>` wildcard MUST NOT appear in SUBJECTS** — a bare wildcard on
  a WorkQueue stream captures every subject including future ones, tying retention
  to any unintended publisher. Enumerate subjects explicitly.
- **DLQ lane = in-stream** — `factory.jobs.dlq.>` is a subject in `FACTORY_JOBS`,
  not a separate stream. The DLQ router (Slice 3, `dlq_router.py`) re-publishes
  from expired advisory subjects back to the main stream via `factory.jobs.dlq.>`.

## Import boundary

`infrastructure/` may import `roxabi_contracts`, `nats`, and stdlib.
Must NOT import `factory.adapters`, `factory.core` business logic, or any HTTP
framework.

## What NOT to do

- ¬add `factory.jobs.omp` to SUBJECTS (see invariant above)
- ¬add `factory.jobs.>` wildcard to SUBJECTS
- ¬change retention to Limits (breaks WorkQueue single-delivery)
- ¬add business logic — provisioning only
