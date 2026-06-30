# Runbook — OTel traces (Collector → otel-raw JSONL)

Headless trace engine per **ADR-097**. Worker and Claude Code spans flow via OTLP gRPC to the collector, which appends JSONL under `~/.local/state/factory/otel/`. The dashboard BFF indexes into SQLite for queries. **Langfuse is not required** in v1.

## Topology

| Unit | Role | Reachable |
|------|------|-----------|
| `factory-otel-collector` | OTLP ingress + JSONL export | `127.0.0.1:4317` (gRPC), `:4318` (HTTP), `:13133` (health) |
| `otel-raw` SQLite | Dashboard query index | `~/.roxabi/factory/otel-raw.db` |
| `factory-langfuse-*` | **Optional / deferred** | not on v1 critical path |

Data: `~/.local/state/factory/otel/spans.jsonl` (not Syncthing-synced).

## Bootstrap (one-time)

```bash
cd ~/projects/roxabi-factory
bash deploy/scripts/bootstrap-otel-raw.sh
make quadlet-install
systemctl --user daemon-reload
```

Start order (v1 — no Langfuse):

```bash
systemctl --user start factory-otel-collector
systemctl --user restart factory-clipool factory-omp
```

## Health

```bash
systemctl --user is-active factory-otel-collector
curl -sf http://127.0.0.1:13133/ && echo collector-ok
ls -la ~/.local/state/factory/otel/spans.jsonl
```

Dashboard raw viewer: `GET /api/bff/spans?pool_id=&job_id=&component=` or UI `/spans`.

## LiteLLM proxy OTel (OMP + cloud relay — secondary)

`llmcli` on M₁ routes OMP and other LiteLLM consumers. Enable OTel in
`~/.roxabi/llmcli/env/proxy.env`:

```bash
LITELLM_OTEL_V2=true
OTEL_EXPORTER=otlp_grpc
OTEL_ENDPOINT=http://factory-otel-collector:4317
OTEL_SERVICE_NAME=llmcli-proxy
OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=no_content
```

## Clipool / Claude Code OTel

`factory-clipool.container` sets Claude Code telemetry → same collector. NATS hook spans (clipool worker) export via `roxabi-otel` when `ROXABI_OTEL_ENABLED=1`.

Disable worker export:

```bash
# ~/.roxabi/factory/env/clipool.env
ROXABI_OTEL_ENABLED=0
```

Disable Claude subprocess traces: `CLAUDE_CODE_ENABLE_TELEMETRY=0`.

## Retention

- Rotate `spans.jsonl` via host `logrotate` (7 days / 5 GiB — see `artifacts/specs/otel-raw-store-spec.mdx`)
- Alert when `~/.local/state/factory/otel/` exceeds 80% of allocated disk

## Langfuse (optional)

To run Langfuse for drill-down experiments, start the `factory-langfuse-*` units manually and set `FACTORY_LANGFUSE_DEFERRED=0` on the dashboard. Collector v1 does **not** export to Langfuse by default.