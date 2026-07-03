# Runbook — NATS/JetStream stream & KV management (M₁)

Ad-hoc stream and KV-bucket administration on `factory-nats` (M₁, `roxabituwer`). For identity
provisioning/retirement see [nats-identity-lifecycle.md](nats-identity-lifecycle.md); for
the ACL model see `docs/architecture/security-routing.md`. This runbook covers **reading** stream
state and **managing** (create/backup/delete) streams and KV buckets once you're authenticated.
It also carries the [outbound-audio deploy/rollback procedure](#outbound-audio-deploy-1482) (#1482).

## Ports

`factory-nats.container` publishes:

- `4222` — client port, all interfaces, plaintext NKey auth (no TLS args needed on `roxabi.network`/tailnet).
- `8222` — HTTP monitor port, `127.0.0.1` only, plaintext, **no auth**.

## Authoritative message-count inventory (no auth required)

The monitor endpoint is the fastest way to check stream health/backlog without minting any
client connection:

```bash
curl -s http://localhost:8222/jsz?streams=1 | jq '.account_details[].stream_detail[] | {name, messages: .state.messages, last_ts: .state.last_ts}'
```

Run this **on M₁** (the port is loopback-only) — via `ssh roxabituwer` if working remotely.

## Stream/KV admin (create, backup, delete)

Use the **hub seed** for any `nats stream`/`nats kv` admin command. The hub identity carries
`$JS.API.>` (full JetStream API wildcard, per the ACL matrix) so it can manage **any** stream —
including legacy ones outside `factory.*` — even though its regular subject grants are scoped to
`factory.*`.

```bash
nats --server nats://localhost:4222 \
     --nkey ~/.roxabi/factory/nkeys/hub.seed \
     --inbox-prefix _inbox.hub \
     <cmd>
```

- `--inbox-prefix _inbox.hub` is **required**. The hub's subscribe allow is `_inbox.hub.>` only
  (not the default `_INBOX.>`), so any request/reply call (every JetStream API call is <!-- drift-ignore -->
  request/reply under the hood) hangs silently without it.
- Plaintext NKey auth on `4222` needs no TLS flags.

### Gotcha: `nats stream ls` hides KV-backed streams

`nats stream ls` does **not** list KV buckets (`KV_*` streams) by default. To see them, either
hit `/jsz` (above, shows everything) or pass `--all`:

```bash
nats --server nats://localhost:4222 --nkey ~/.roxabi/factory/nkeys/hub.seed --inbox-prefix _inbox.hub \
     stream ls --all
```

### Before you delete (blast radius)

- **Hub-provisioned streams** (`FACTORY_*`, `KV_*` used by hub/adapters) are recreated empty on
  boot — `stream rm --force` drops **message history** and can crash-loop consumers until
  reprovisioned. Converge restarts units but does not restore JetStream data.
- Stop affected units before destructive ops on live infra (see the
  [outbound-audio deploy order](#outbound-audio-deploy-1482) for stream-touching changes).
- Back up to a host path **outside** `factory-jetstream.volume` (`~/.roxabi/factory/nats/jetstream`)
  — backups co-located on the volume are deleted with the stream.

### Deleting a KV bucket

A KV bucket is just a stream named `KV_<bucket>`. **Back up first** (see below), then:

```bash
nats --server nats://localhost:4222 --nkey ~/.roxabi/factory/nkeys/hub.seed --inbox-prefix _inbox.hub \
     stream rm KV_<bucket> --force
```

### Reversible delete (always do this for anything non-trivial)

Back up before removing — `nats stream restore` can bring it back only if the stream is absent
and backup data lives off the JetStream volume:

```bash
BACKUP_ROOT="/var/backups/nats/$(date +%Y%m%dT%H%M%S)"
mkdir -p "$BACKUP_ROOT"

nats --server nats://localhost:4222 --nkey ~/.roxabi/factory/nkeys/hub.seed --inbox-prefix _inbox.hub \
     stream backup <STREAM> "$BACKUP_ROOT"

nats --server nats://localhost:4222 --nkey ~/.roxabi/factory/nkeys/hub.seed --inbox-prefix _inbox.hub \
     stream rm <STREAM> --force

# to undo (stream must not exist — verify with stream ls / jsz first):
nats --server nats://localhost:4222 --nkey ~/.roxabi/factory/nkeys/hub.seed --inbox-prefix _inbox.hub \
     stream restore "$BACKUP_ROOT"
```

## Outbound-audio deploy (#1482)

JetStream-backed outbound audio: subjects `factory.outbound.audio.<platform>.<bot_id>`, stream `FACTORY_OUTBOUND_AUDIO`, KV `factory_outbound_audio_sent`. ADR-079 (supersedes ADR-077). The deploy of #1482 is consumed; the durable parameters, deploy order, and rollback live here.

### Parameters

| Parameter | Value |
|---|---|
| Stream | `FACTORY_OUTBOUND_AUDIO` |
| Subjects | `factory.outbound.audio.>` |
| Retention | Limits (multi-consumer fan-out) |
| MaxAge | 24 h · MaxBytes 32 MiB |
| Consumers | `outbound-audio-telegram`, `outbound-audio-discord` |
| AckWait | 90 s · MaxDeliver 5 |
| KV TTL | 900 s |

### Deploy order (strict)

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

### Verify

```bash
nats stream info FACTORY_OUTBOUND_AUDIO
nats consumer info FACTORY_OUTBOUND_AUDIO outbound-audio-telegram
nats consumer info FACTORY_OUTBOUND_AUDIO outbound-audio-discord
systemctl --user status factory-telegram factory-discord
journalctl --user -u factory-telegram -n 50 | grep -E "audio|FACTORY_OUTBOUND"
```

Monitoring probes: `audio:consumer_lag` (`num_pending > 50`), `audio:stream_usage` (> 80% of 32 MiB).

### Rollback

1. Pin hub image to pre-#1482 tag → `systemctl --user restart factory-hub`
2. Roll back adapter images → `systemctl --user restart factory-telegram factory-discord`

Stream/KV can remain (messages age out in 24 h). Optional purge:

```bash
nats stream rm FACTORY_OUTBOUND_AUDIO --force
nats stream rm KV_factory_outbound_audio_sent --force
```

Dormant bucket (safe to delete since #1777): `nats kv del factory-turns-meta`

Source refs: `deploy/nats/acl-matrix.json`, `src/factory/infrastructure/outbound_audio/stream_setup.py`, `src/factory/bootstrap/standalone/audio_consumer_bootstrap.py`, `src/factory/monitoring/checks_audio.py`.

## See also

- [nats-identity-lifecycle.md](nats-identity-lifecycle.md) — adding/retiring NATS identities, seed propagation across hosts
- `docs/architecture/security-routing.md` — ACL model, per-identity inbox prefixes (ADR-051), request/reply derivation (ADR-064)
- `docs/history/nats-acl-inbox-case-postmortem.md` — history of the inbox-prefix normalization that makes `--inbox-prefix` mandatory today
