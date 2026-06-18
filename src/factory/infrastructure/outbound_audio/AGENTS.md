# src/factory/infrastructure/outbound_audio/ — JetStream + KV Provisioning

## Purpose

Idempotent JetStream stream + durable consumer + KV bucket bootstrap for
the durable outbound-audio delivery path (#1482).

## Invariants

- **Hub sole-provisioner (ADR-079)** — `ensure_stream` and `ensure_kv` are called
  by `hub_standalone.py` before `announce_hub_ready`. Adapters must NOT call these;
  `start_audio_consumer` is bind-only for the KV (`js.key_value(KV_BUCKET)`).
  `ensure_consumer` remains on the adapter (per-bot durable consumer, not shared).
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
Must NOT import `factory.adapters`, `factory.core` business logic, or any HTTP
framework. `STREAM_AUDIO` is imported from `roxabi_contracts.outbound`.

## What NOT to do

- ¬change retention to WorkQueue (breaks multi-consumer fan-out)
- ¬lower KV TTL below 450 s (floor = ack_wait × max_deliver)
- ¬add business logic here — this module is infrastructure provisioning only
- ¬import adapters or core business objects
