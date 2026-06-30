# Runbook — OTel traces (factory-otel → otel-raw JSONL)

Headless trace engine per **ADR-097**. Worker and Claude Code spans flow via OTLP gRPC to **`factory-otel`** (in-process store, blobstore-style), which appends JSONL under `~/.local/state/factory/otel/` and maintains a SQLite index. The dashboard BFF queries spans over HTTP. **Langfuse is not required** in v1.

## Topology

| Unit | Role | Reachable |
|------|------|-----------|
| `factory-otel` | OTLP ingest (gRPC) + JSONL/SQLite store + query API | `:4317` (gRPC, pod network), `:8450` (HTTP `/healthz`, `/api/spans`) |
| `otel-raw` SQLite | Span index (inside factory-otel) | `~/.roxabi/factory/otel-raw.db` |
| `factory-langfuse-*` | **Optional / deferred** | not on v1 critical path |

Data: `~/.local/state/factory/otel/spans.jsonl` (not Syncthing-synced).

**Deprecated:** `factory-otel-collector` (upstream `otelcol-contrib` image) is disabled in `deploy/quadlet.toml`. Kept on disk for rollback reference only.

## Bootstrap (one-time)

```bash
cd ~/projects/roxabi-factory
bash deploy/scripts/bootstrap-otel-raw.sh
make quadlet-install
systemctl --user daemon-reload
```

`install.sh` generates `factory_otel_token` for dashboard ↔ factory-otel bearer auth.

Start order (v1 — no Langfuse):

```bash
systemctl --user start factory-otel
systemctl --user restart factory-clipool factory-omp
```

## Health

```bash
systemctl --user is-active factory-otel
curl -sf http://127.0.0.1:8450/healthz && echo otel-ok
ls -la ~/.local/state/factory/otel/spans.jsonl
```

Dashboard raw viewer: `GET /api/bff/spans?pool_id=&job_id=&component=` or UI `/spans`.

## LiteLLM proxy OTel (OMP + cloud relay — Block 7)

`llmcli` on M₁ routes OMP and other LiteLLM consumers. Enable OTel in
`~/.roxabi/llmcli/env/proxy.env`:

```bash
LITELLM_OTEL_V2=true
OTEL_EXPORTER=otlp_grpc
OTEL_ENDPOINT=http://factory-otel:4317
OTEL_SERVICE_NAME=llmcli-proxy
OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=no_content
```

## Clipool / Claude Code OTel

`factory-clipool.container` sets Claude Code telemetry → `factory-otel:4317`. NATS hook spans (clipool worker) export via `roxabi-otel` when `ROXABI_OTEL_ENABLED=1`.

Disable worker export:

```bash
# ~/.roxabi/factory/env/clipool.env
ROXABI_OTEL_ENABLED=0
```

Disable Claude subprocess traces: `CLAUDE_CODE_ENABLE_TELEMETRY=0`.

## Satellite workers (Blocks 6–7 — external repos)

After factory packages merge to `staging`, bump `roxabi-nats` + `roxabi-otel` in each satellite and set:

```bash
ROXABI_OTEL_ENABLED=1
OTEL_EXPORTER_OTLP_ENDPOINT=http://factory-otel:4317
```

| Repo | Span attrs (via `telemetry_attributes`) |
|------|----------------------------------------|
| voiceCLI | STT: `roxabi.blob_ref.in`, `roxabi.model` · TTS: `roxabi.blob_ref.out` |
| imageCLI | `roxabi.blob_ref.out`, `roxabi.engine` |
| llmCLI worker | `roxabi.model` |
| llmCLI proxy | LiteLLM OTel v2 (see above) |

Rollout checklist: `artifacts/specs/otel-satellite-rollout-spec.mdx`.

## Retention

- Rotate `spans.jsonl` via host `logrotate` (7 days / 5 GiB — see `artifacts/specs/otel-raw-store-spec.mdx`)
- Alert when `~/.local/state/factory/otel/` exceeds 80% of allocated disk

## Langfuse (optional)

To run Langfuse for drill-down experiments, start the `factory-langfuse-*` units manually and set `FACTORY_LANGFUSE_DEFERRED=0` on the dashboard. factory-otel v1 does **not** export to Langfuse.