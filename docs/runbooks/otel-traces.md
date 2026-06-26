# Runbook — OTel traces (Collector → Langfuse)

Headless trace engine per **ADR-092**. Claude Code spans flow clipool → OTel Collector → Langfuse. Logs stay on Loki; metrics are a later phase.

## Topology

| Unit | Role | Reachable |
|------|------|-----------|
| `factory-otel-collector` | OTLP ingress | `127.0.0.1:4317` (gRPC), `:4318` (HTTP) |
| `factory-langfuse-web` | Trace UI + OTLP ingest | `http://127.0.0.1:3000` |
| `factory-langfuse-*` | Postgres, ClickHouse, Redis, MinIO, worker | internal (`roxabi.network`) |

Data: `~/.local/state/factory/langfuse/` (not Syncthing-synced).

## Bootstrap (one-time)

```bash
cd ~/projects/roxabi-factory
bash deploy/scripts/bootstrap-langfuse.sh
make quadlet-install   # or manual cp per quadlet-install runbook
systemctl --user daemon-reload
```

Start order: deps → worker → web → collector → restart clipool.

```bash
systemctl --user start factory-langfuse-postgres factory-langfuse-clickhouse \
  factory-langfuse-redis factory-langfuse-minio
sleep 15
systemctl --user start factory-langfuse-worker factory-langfuse-web
sleep 30
systemctl --user start factory-otel-collector
systemctl --user restart factory-clipool
```

## Health

```bash
systemctl --user is-active factory-otel-collector factory-langfuse-web \
  factory-langfuse-worker factory-langfuse-postgres
curl -sf http://127.0.0.1:3000/api/public/health && echo langfuse-ok
curl -sf http://127.0.0.1:13133/ && echo collector-ok   # via host publish if enabled
```

Langfuse UI: `http://127.0.0.1:3000` — credentials printed once by `bootstrap-langfuse.sh`.

## LiteLLM proxy OTel (OMP + cloud relay — secondary)

`llmcli` on M₁ routes OMP and other LiteLLM consumers. Enable OTel in
`~/.roxabi/llmcli/env/proxy.env` — `LLMCLI_OTEL_ENABLED=1` auto-injects
`callbacks: ["otel"]` at proxy startup (LiteLLM 1.86.x v1 integration):

```bash
LLMCLI_OTEL_ENABLED=1
OTEL_EXPORTER_OTLP_ENDPOINT=http://factory-otel-collector:4317
OTEL_EXPORTER_OTLP_PROTOCOL=grpc
OTEL_SERVICE_NAME=llmcli-proxy
OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT
```

```bash
systemctl --user restart llmcli
podman exec llmcli env | grep -E 'LLMCLI_OTEL|OTEL_EXPORTER'
```

Template: `llmCLI/deploy/proxy.env.example`. OMP spans appear in Langfuse when
`factory-omp` calls `http://llmcli:18091`.

## Clipool / Claude Code OTel

`factory-clipool.container` sets:

- `CLAUDE_CODE_ENABLE_TELEMETRY=1`
- `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1` (span tracing)
- `OTEL_TRACES_EXPORTER=otlp` → `http://factory-otel-collector:4317` (gRPC)
- `OTEL_LOGS_EXPORTER=none`, `OTEL_METRICS_EXPORTER=none` (avoid duplicating Loki)
- `OTEL_LOG_USER_PROMPTS=0`, `OTEL_LOG_TOOL_DETAILS=0` (PII off by default)

Disable traces: set `CLAUDE_CODE_ENABLE_TELEMETRY=0` in `~/.roxabi/factory/env/clipool.env` and restart clipool.

## Manual OTLP smoke test

```bash
# From host — HTTP/protobuf test span (requires grpcurl or otel-cli)
curl -sf -X POST http://127.0.0.1:4318/v1/traces \
  -H 'Content-Type: application/json' \
  -d '{"resourceSpans":[]}' || true
```

After a real Claude turn via Telegram/Discord, open Langfuse → Traces and filter by recent time.

## Incident triage

| Symptom | Check |
|---------|-------|
| No traces in Langfuse | `journalctl --user -u factory-otel-collector -u factory-langfuse-web --since=10m` |
| Collector 401 to Langfuse | Re-run `bootstrap-langfuse.sh` (refreshes `otel-collector.env`) |
| Clipool spans missing | `podman exec factory-clipool env \| grep OTEL` |
| Langfuse crash-loop | ClickHouse/Postgres data perms under `~/.local/state/factory/langfuse/` |

## Auth files

| File | Purpose |
|------|---------|
| `~/.roxabi/factory/env/langfuse.env` | Stack secrets + headless init API keys |
| `~/.roxabi/factory/env/otel-collector.env` | `LANGFUSE_OTEL_AUTH` (base64 `pk:sk`) |

Never commit these files. Rotate API keys via Langfuse UI → update `otel-collector.env`.

## Future (#1760)

Control-plane dashboard composes Langfuse trace views — operators stop opening `:3000` directly.