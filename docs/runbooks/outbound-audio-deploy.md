# Runbook — Outbound audio deploy (#1482)

JetStream-backed outbound audio: subjects `factory.outbound.audio.<platform>.<bot_id>`, stream `FACTORY_OUTBOUND_AUDIO`, KV `factory_outbound_audio_sent`. ADR-079 (supersedes ADR-077).

## Parameters

| Parameter | Value |
|---|---|
| Stream | `FACTORY_OUTBOUND_AUDIO` |
| Subjects | `factory.outbound.audio.>` |
| Retention | Limits (multi-consumer fan-out) |
| MaxAge | 24 h · MaxBytes 32 MiB |
| Consumers | `outbound-audio-telegram`, `outbound-audio-discord` |
| AckWait | 90 s · MaxDeliver 5 |
| KV TTL | 900 s |

## Deploy order (strict)

**1 — ACL → regen `auth.conf` → restart `factory-nats`**

```bash
factory-acl genkeys --regen-authconf
systemctl --user restart factory-nats
```

Adapters call JetStream API on boot (`ensure_stream`, `ensure_consumer`, `ensure_kv`). Without ACL grants, adapters crash at bootstrap.

**2 — Restart hub** (publishes to `factory.outbound.audio.*`)

```bash
systemctl --user restart factory-hub
```

**3 — Restart adapters** (self-provision stream/consumer/KV, start pull consumer)

```bash
systemctl --user restart factory-telegram factory-discord
```

No separate stream-creation step — adapters provision on start.

## Verify

```bash
nats stream info FACTORY_OUTBOUND_AUDIO
nats consumer info FACTORY_OUTBOUND_AUDIO outbound-audio-telegram
nats consumer info FACTORY_OUTBOUND_AUDIO outbound-audio-discord
systemctl --user status factory-telegram factory-discord
journalctl --user -u factory-telegram -n 50 | grep -E "audio|FACTORY_OUTBOUND"
```

Monitoring probes: `audio:consumer_lag` (`num_pending > 50`), `audio:stream_usage` (> 80% of 32 MiB).

## Rollback

1. Pin hub image to pre-#1482 tag → `systemctl --user restart factory-hub`
2. Roll back adapter images → `systemctl --user restart factory-telegram factory-discord`

Stream/KV can remain (messages age out in 24 h). Optional purge:

```bash
nats stream rm FACTORY_OUTBOUND_AUDIO --force
nats stream rm KV_factory_outbound_audio_sent --force
```

Dormant bucket (safe to delete since #1777): `nats kv del factory-turns-meta`

## References

- `deploy/nats/acl-matrix.json`
- `src/factory/infrastructure/outbound_audio/stream_setup.py`
- `src/factory/bootstrap/standalone/audio_consumer_bootstrap.py`
- `src/factory/monitoring/checks_audio.py`
