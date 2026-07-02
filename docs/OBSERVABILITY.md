# Observability

> **SSoT moved** → [`docs/architecture/observability.md`](architecture/observability.md)
>
> That domain page is the living current-truth for the observability planes,
> the control-plane dashboard, the trace engine (otel-raw), logs (Loki +
> promtail), operator audit, the fleet/pipeline read models, and ingress.
> This file is a thin operational pointer only — do not add architecture
> content here.

## Where to look

| Question | Go to |
|---|---|
| Architecture, planes, invariants, what is current vs target | [`architecture/observability.md`](architecture/observability.md) |
| Deploy audit trail, "did converge run?", rotations | [`runbooks/operator-log.md`](runbooks/operator-log.md) |
| Central log search (LogQL, journald labels) | [`runbooks/loki-query.md`](runbooks/loki-query.md) |
| Trace pipeline ops (factory-otel, spans store, tokens) | [`runbooks/otel-traces.md`](runbooks/otel-traces.md) |
| Webhook ingress setup and debugging | [`runbooks/ingress-webhooks.md`](runbooks/ingress-webhooks.md) |

## Quick recipes

Isolate one turn's log lines (every turn gets a `trace_id`; the `pool_id`
identifies the conversation scope — both are injected into every log record):

```bash
journalctl --user -u factory-hub | grep '<trace-id>'
journalctl --user -u factory-hub | grep 'telegram:main:chat:123456'
```

Log level is `INFO` by default; override via `[logging] level` in
`config.toml`. Containers log plaintext to stdout → journald; message content
is never logged (only counts) — full text lives in the turn store
(`architecture/storage.md`).
