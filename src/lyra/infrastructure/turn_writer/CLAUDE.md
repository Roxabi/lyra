# src/lyra/infrastructure/turn_writer/ — JetStream Subscriber-Writer

## Purpose

Sole writer to `turns.db` post-refactor (#1331). Subscribes to
`lyra.turns.write` JetStream subject and persists turn events via
`TurnStore` private mutators.

## ADR

ADR-075 authorises this sublayer (axial: `stage-of-pipeline` →
`infrastructure` → writer-subscriber sublayer).

## Invariants

- **Sole writer**: only this subsystem mutates `turns.db`. Hub + adapters
  mount the file read-only (per `deploy/quadlet/lyra-hub.container` T26).
- **Durable consumer**: `LYRA_TURNS` stream + `turn-writer-v1` consumer
  with AckExplicit, AckWait=60s, MaxDeliver=5, MaxAge=24h, WorkQueue
  retention. Horizontal scaling via shared durable name (queue group
  semantics; `queue_group` on pull consumers is not supported by nats-py
  — fan-out via the durable identity).
- **Idempotence per kind** (event_id-agnostic for natural keys):
  - `log_turn` → UNIQUE(platform, message_id) catches replays
  - `start_session` / `end_session` / `set_cli_session` → SQL is
    structurally idempotent (INSERT OR IGNORE / UPDATE / INSERT OR REPLACE)
  - `increment_resume_count` → high-water mark via
    `UPDATE … SET resume_count = max(resume_count, ?)` + `processed_events`
    table catches event_id replays
- **No public mutator API**: writer calls `_log_turn`, `_start_session`,
  `_end_session`, `_set_cli_session` on TurnStore. Public mutator names
  no longer exist (privatised in T9).

## Process boundary

Runs as its own systemd unit: `lyra-turn-writer.container`. Entry point:
`lyra turn-writer` CLI subcommand → `_bootstrap_turn_writer_standalone`.

NATS user: `turn-writer` (subscribes `lyra.turns.>`, publishes _INBOX.>
for ACK path, JetStream API scoped to `LYRA_TURNS` + `turn-writer-v1`
only — see `deploy/nats/auth.conf`).

## Stream / consumer bootstrap

`stream_setup.py` exposes `ensure_stream(js)` and `ensure_consumer(js)`.
Both use the add → BadRequestError → update / consumer_info →
NotFoundError → add_consumer pattern (idempotent — safe to call on every
process start).

## Health + metrics

`health.py` exposes `GET /health` (writer task + NATS + DB liveness) and
`GET /metrics` (Prometheus text exposition with
`turn_writer_lag_seconds`). Default port 8083; override via
`LYRA_TURN_WRITER_HEALTH_PORT`.

## What NOT to do

- ¬add a second writer of `turns.db` anywhere in the tree
- ¬expose public mutators on TurnStore (would re-open the duplication N×M trap)
- ¬change ack semantics without re-running the crash-recovery test (SC-8)
- ¬widen the NATS user's JetStream API allow-list beyond LYRA_TURNS
