# src/lyra/infrastructure/outbound_audio/ — JetStream + KV Provisioning

## Purpose

Idempotent JetStream stream + durable consumer + KV bucket bootstrap for
the durable outbound-audio delivery path (#1482).

## Stream / consumer / KV

`stream_setup.py` exposes three public coroutines:

- `ensure_stream(js)` — create or update `LYRA_OUTBOUND_AUDIO` stream.
- `ensure_consumer(js, *, durable, filter_subject)` — create durable pull
  consumer if absent. Bootstrap calls this once per platform adapter.
- `ensure_kv(js)` — create or bind KV bucket `lyra_outbound_audio_sent`;
  returns a `KeyValue` handle.

All three use the add→BadRequestError→update / consumer_info→NotFoundError→add
pattern (idempotent — safe to call on every process boot).

## Invariants

- **Retention: Limits** — NOT WorkQueue. Multiple per-platform consumers attach
  to the same stream (N×M fan-out). WorkQueue would delete messages after first
  delivery, starving subsequent consumers.
- **KV TTL = 900 s (15 min)** — arithmetic: ack_wait=90s × max_deliver=5 = 450s
  floor; 900s provides ≥2× headroom for retry jitter and slow consumers while
  bounding dedup-key storage. Do not lower below 450s.
- **MaxAge = 24 h** — silent-loss bound (D4). Messages older than 24 h are
  dropped by NATS regardless of delivery state.
- **MaxBytes = 32 MiB** — headroom for voice payloads; tune up if burst
  throughput warrants it.
- **AckWait = 90 s / MaxDeliver = 5** — gives ~7.5 min total retry window
  per message before it becomes undeliverable (dead-letter handling is T5).

## Import boundary

`infrastructure/` may import `roxabi_contracts`, `nats`, and stdlib.
Must NOT import `lyra.adapters`, `lyra.core` business logic, or any HTTP
framework. `STREAM_AUDIO` is imported from `roxabi_contracts.outbound`.

## What NOT to do

- ¬change retention to WorkQueue (breaks multi-consumer fan-out)
- ¬lower KV TTL below 450 s (floor = ack_wait × max_deliver)
- ¬add business logic here — this module is infrastructure provisioning only
- ¬import adapters or core business objects
