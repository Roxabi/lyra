---
title: Observability & Control Plane
description: Living current-truth document for the observability planes, engines, dashboard control plane, operator audit, and external-event ingress in factory.
---

# Observability & Control Plane — factory

> Status: LIVING — current truth for observability + control-plane decisions.
> Last updated: 2026-08-01.
> Source ADRs: 091, 092, 093, 094, 096, 097, 098, 104.

## Scope

This document covers the four observability planes and their boundary rules, the
control-plane dashboard as the sole human surface, the engines behind it (trace
engine v1, log engine), per-turn trace IDs, the operator audit channels, the
fleet + PR-pipeline read models, and the ingress connector / tenant registry.
It does not cover job-plane semantics (see `job-model.md`), domain security
audit payloads (see `security-routing.md`), store internals such as the turn
store (see `storage.md`), or deploy mechanics (see `deployment.md`).

## Current state

### Four observability planes

Every operational signal belongs to exactly one plane (ADR-091). The plane
decides the grammar, the producer set, and the consumption mode:

| Plane | Grammar | Role | Consumption |
|---|---|---|---|
| ① External events | `factory.event.>` (JetStream stream `factory-events`, 24 h) | World pushes discrete signals (webhooks, host sensors) | JetStream subscribe — hot triggers + cold window replay |
| ② Internal jobs | `factory.job.<id>.*` subject family | Factory orchestrates observable work | Job plane — see `job-model.md` |
| ③ Internal infra state | HTTP health endpoints (pull) + `factory.metric.>` (JetStream stream `factory-metrics`, 7 d) | Runtime snapshot — "is the factory healthy?" | Pull for health; fleet reports pushed on `factory.metric.host.container_report` |
| ④ Read models | Off-bus stores (`pipeline.db`, fleet state, turn store) | Enrichment lookups and projections | Point reads / hub RPC — never bus publishes |

Boundary rules (normative, ADR-091):

- Job lifecycle stays on the job plane — terminal state is the job-plane
  subject family; no parallel `factory.event.*` re-emission of the same fact.
- External pushes land on the event plane; infra health is state, not world.
- Read models are not events — a store lookup after an event is enrichment,
  not a bus publish.
- Sensors publish facts; the hub decides. No sensor or ingress connector may
  alert, dispatch jobs, or apply policy directly.

Both event streams are provisioned idempotently at hub boot
(`src/factory/infrastructure/events/stream_setup.py`). The typed envelopes
live in `roxabi_contracts.event` (event + metric models and subject helpers
`per_service_event`, `per_connector_tenant_event`, `per_service_metric`).

The Sentinelle consumer itself (hub module that subscribes events, enriches,
and alerts on Discord) is ratified design — it is not yet implemented in
`src/factory`. Current live consumers of plane ① are the hub read-model
projectors (below).

### Host presence (edge → plane ①)

Workstation attention / presence is **not** owned as a second collector stack
inside the factory monorepo. Capture lives in **roxabi-sense** (local SQLite +
CLI/MCP). Factory owns the **NATS contract** and **consumption** (ADR-104).

| Role | Owner |
|------|--------|
| Subjects | `factory.event.host.<machine>.activity` \| `.stale` (via `publish_host_event`) |
| Payload SSOT | ADR-104 (coarse: presence, sources, confidence, degraded — **no titles**) |
| Publish client (opt-in) | roxabi-sense surface (issue [Roxabi/roxabi-sense#7](https://github.com/Roxabi/roxabi-sense/issues/7)) |
| Subscribe + policy | factory hub Sentinelle (or interim log consumer) |
| Parallel `factory-host-sensor` binary | **Rejected** — role = sense (re-scopes #2007) |

Sequence: **factory contract + consumer first** → sense thin publisher second.
Helper already exists: `src/factory/ops/host_event.py`.

**Standalone log monitor (`factory-log-monitor`, #2245) — plane ③, V1-pull.**
A dedicated Quadlet container (`deploy/quadlet/factory-log-monitor.container`)
periodically pulls NATS/hub container logs from Loki (`src/factory/monitoring/
log_watch.py`, `checks_log.py`) and fires a raw Telegram alert
(`escalation.py`) on threshold breach — no NATS connection, no
`factory.metric.>` producer. It is explicitly a thin, temporary safety net:
pull, not subscribe (plane ③'s existing HTTP-health mode, not a new plane),
and it is not a preview of Monitoring v2/Sentinelle (no event bus, no
dashboard surface). It should be retired or subsumed once Monitoring v2
(#1035) ships log-scanning as a hub-native plane ③/④ consumer — see the
`factory.monitoring` package docstring and `docs/CONFIGURATION.md`'s
Monitoring section for the dormant-vs-live module split.

### Control-plane dashboard — sole human surface

The dashboard is the **only** operator-facing surface (ADR-092). Every engine
runs headless behind it; operators do not open per-engine UIs. Grafana was
explicitly dropped as a surface.

ADR-094 consolidated the control plane into the existing web adapter container
rather than forking a parallel adapter stack: one HTTP process
(`factory-dashboard` Quadlet unit), two internal axes:

| Axis | Code | Path |
|---|---|---|
| Chat adapter | `WebAdapter` in `src/factory/adapters/web/` | Operator chat over the messaging plane — `factory.inbound.web.>` / `factory.outbound.web.>` |
| Control-plane BFF | `src/factory/dashboard/` | `/api/bff/` panels — spans, fleet, pipeline, jobs, sessions, ops health/logs |

Transitional naming (phase 1 of ADR-094): the container is
`factory-dashboard` but the NATS platform token is still `web` with
`bot_id=smoke`; platform rename and operator session auth are phase 2.

**BFF read-path rule (ADR-097 amendment to ADR-094):** the BFF reads
control-plane state via **hub NATS RPC** (`factory.dashboard.>` request-reply,
e.g. `factory.dashboard.pipeline.list`, `factory.dashboard.fleet.list`) plus
SSE fanout to browsers. The BFF must **not** subscribe to `factory.event.>` or
`factory.metric.>` directly — the hub owns projectors and read models
(plane ④). Engine queries (Loki, factory-otel) are HTTP proxies with bearer
auth, not hub chat routing.

### Split transport — push vs pull

ADR-092 fixes the transport split:

| Transport | Carries | Status |
|---|---|---|
| NATS push | Live + control plane — `factory.event.>`, `factory.metric.>`, job lifecycle | **Current** — events, fleet reports, hub RPC |
| Prometheus pull (scrape) + Alertmanager routing | History, rate/range queries, alert rules → Telegram/ntfy/email | **Target only** — no Prometheus or Alertmanager unit is deployed today |

Until the pull side ships, history lives in the JetStream retention windows,
the otel-raw store, and Loki; alerting is manual (dashboard + runbooks).

### Trace engine v1 — otel-raw

Trace plane v1 is the raw OTLP store (ADR-097), not Langfuse: collect cleanly
now, post-process later. The `factory-otel` service (single factory image,
`factory otel serve`) ingests OTLP gRPC, appends JSONL, indexes into SQLite,
and serves an authenticated HTTP query API:

| Layer | Choice |
|---|---|
| Ingest | OTLP gRPC (workers, clipool agent OTel, LiteLLM proxy) |
| Archive | JSONL `~/.local/state/factory/otel/spans.jsonl` (logrotate) |
| Query index | SQLite `~/.roxabi/factory/otel-raw.db` — keyed by trace/job/pool/time |
| Dashboard | BFF spans endpoint proxies the factory-otel HTTP API (bearer token) |
| Langfuse | **Deferred** — its Quadlet units are `disabled` in `deploy/quadlet.toml`; no Langfuse exporter in v1 |

The legacy upstream `factory-otel-collector` unit is likewise disabled — kept
on disk for rollback reference only.

**Three-store contract** (normative — ADR-097):

| Store | Holds | OTel relationship |
|---|---|---|
| blobstore | Bytes (audio, images, attachments) | Spans carry blob refs only — never inline bytes |
| turn store | Conversation text (L1 audit — see `storage.md`) | Join via job/pool IDs — no text duplication in spans |
| otel-raw | Timeline metadata (spans, attrs, durations) | SSoT v1 for post-processing + raw viewer |

**Instrumentation boundary** (axial — one hook site, not N×M per worker):

| Package | OTel responsibility |
|---|---|
| `roxabi_contracts.telemetry` | `MessageLifecycleHooks` protocol + span attribute registry |
| `roxabi-nats` | Injectable hooks in `NatsAdapterBase` — zero OpenTelemetry imports |
| `roxabi-otel` | `OtelLifecycleHooks`, `NoopHooks`, OTLP export, in-memory test recorder |
| Hub | `TraceMiddleware` mints correlation fields at ingress |

Attribute scrubbing runs on both sides of the wire: at export
(`roxabi_otel.scrub`) and again at ingest (`factory.otel.scrub_otlp`) — spans
must never carry secret material. Rollback is config, not redeploy:
`ROXABI_OTEL_ENABLED=0` swaps workers to `NoopHooks`
(`src/factory/obs/otel_wiring.py`).

**OTel source hierarchy** (ADR-092): Claude Code OTel is the **primary** agent
trace source — agents bypass the LiteLLM proxy, so LiteLLM telemetry alone is
structurally blind to agent turns. LiteLLM is the secondary source for the
worker paths that do traverse the proxy. The trace plane is decoupled from the
distributed harness; only cross-NATS span nesting remains deferred (v1 accepts
sibling spans).

### Log engine — Loki + promtail

| Unit | Role |
|---|---|
| `factory-loki` | Log store + LogQL API, bound to localhost |
| `factory-promtail` | Ships operator JSONL + user journald (owner-UID filter, factory/voice unit filter) |

Config lives in `deploy/observability/` (`loki-config.yml`,
`promtail-config.yml`). The dashboard ops panel proxies Loki through the BFF
(`src/factory/dashboard/ops_proxy.py`); `logcli` remains a field fallback —
see `docs/runbooks/loki-query.md`.

Application logs are structured stdout → journald. Every inbound turn gets a
`trace_id` minted by `TraceMiddleware` and propagated through the async call
chain via contextvars; `TraceIdFilter` (`src/factory/core/trace.py`) injects
`trace_id` + `pool_id` into every log record, so one grep isolates a turn and
the pool ID reconstructs a conversation scope. Message content is never
logged — only counts; full text belongs to the turn store (`storage.md`).

### Operator audit — three channels

Deploy/operator imperative actions are audited separately from application
runtime (ADR-093):

| Channel | Sink | Scope |
|---|---|---|
| Operator JSONL | `~/.local/state/factory/logs/operator.log` | `deploy/install.sh`, converge, lock events, blobstore regen/skip |
| Container + timer runtime | journald `--user` | Quadlet stdout/stderr, deploy timers |
| Credential rotation narrative | `~/.roxabi/factory/rotation-log.md` | Voluntary secret changes, human-readable, synced |

Triage rule: runtime failures → journald first; deploy history →
operator.log; "who rotated what?" → rotation-log + operator.log events.
Implementation is `deploy/lib/operator-log.sh`; logging is append-only and
fail-soft (audit must never break a deploy). **No channel may contain secret
bytes** — no token bytes, seed contents, env values, or secret-bearing argv.
Promtail ships channels 1–2 into Loki with stable labels; the three channels
remain the source of truth regardless of Loki availability.

Domain security audit (blob access, CLI spawn security) is a separate
JetStream stream and stays out of operator.log — see `security-routing.md`.

### Fleet + PR-pipeline read models

Plane ④ projectors live **in the hub**, not in the dashboard (ADR-098):

- **Fleet:** every instrumented container runs a `FleetReporter`
  (`packages/roxabi-obs/`) publishing periodic container reports on
  `factory.metric.host.container_report` (image revision, health, uptime).
  The hub ingests them (`src/factory/bootstrap/fleet_ingest.py`) into
  `FleetStore`; liveness staleness ships in v1, registry-digest staleness is
  deferred.
- **PR pipeline:** the hub projects ingress events into `PipelineStore`
  (`src/factory/nats/pipeline/`, persisted at `~/.roxabi/factory/pipeline.db`)
  via `src/factory/bootstrap/pipeline_ingest.py`, replaying the 24 h
  `factory-events` window on cold start. Stage machine per PR:
  ci → reviewed → merged → publish → m1_deploy → cf_deploy.

M₁ deploy proof is **fleet quorum**: every container in `M1_DEPLOY_QUORUM`
must report ok on the published SHA within a freshness window. The converge
certificate event (`deploy/converge.sh` publishes a host converge event via
`factory ops publish-host-event`) records metadata only — it never overrides
fleet proof. CF proof covers Pages deployments via registered project mapping;
Workers releases are only visible through GitHub CI.

**Non-authoritative UX (normative):** the pipeline panel is indicative, never
canonical — banner "NOT FOR MERGE DECISIONS", explicit staleness display, and
`pending`/`unknown` preferred over false green on deploy stages.

### Ingress — connector registry + tenant scoping

`factory-ingress` (dedicated M₁ container; internet-reachable via the
`factory-cloudflared` tunnel unit) receives external webhooks and publishes
plane ① events. ADR-096 froze a two-axis contract before connector/tenant
counts grow:

- **Connectors are code plugins** — each implements verify /
  parse-external-id / normalize behind `ConnectorRegistry`
  (`src/factory/ingress/connectors/`); routes are generic, adding a provider
  is one plugin + registration, zero route edits.
- **Tenants are data** — `InstallationRegistry` maps provider-native external
  IDs to a `factory_tenant` slug in a dedicated `~/.roxabi/factory/ingress.db`
  (RW-mounted on ingress only; deliberately not `auth.db`/`config.db`).
  Published tenant always comes from registry lookup — never from URL path or
  payload alone.
- **Subject grammar:** ingress connectors publish 4-segment
  `factory.event.{connector}.{tenant}.{kind}` (ACL grants are
  `factory.event.github.>` / `factory.event.cloudflare.>`); non-ingress
  plane ① producers keep 3-segment `factory.event.{service}.{kind}`.
- **Secrets are hybrid:** centralized-app connectors (GitHub-style) get one
  static secret per connector; per-account-provision connectors get one secret
  per provisioned webhook (`SecretResolver` protocol).
- **Unknown-installation drop policy:** verify signature first, resolve
  tenant, and on miss log + count + return the same accepted response as
  success without publishing — no information leak, no crash.

V1 runs a single tenant (`default`); dashboard-driven onboarding is deferred
behind operator auth. Interim registry CRUD is the ingress CLI
(`src/factory/ingress/cli.py`) — see `docs/runbooks/ingress-webhooks.md`.

> **Clustering note:** ingress sits in this domain because it *feeds* plane ①
> and the pipeline read model, but ADR-096 is really a **connector/integration
> registry** decision (webhook verification, tenancy, secrets). If the
> connector surface grows (Vercel, mail, self-service onboarding), it should
> graduate to its own integration domain page rather than stretch this one.

### Target-only (not deployed)

Labeled target to prevent doc-drift-by-optimism:

- Prometheus scrape + Alertmanager routing (ADR-092 pull side).
- `factory.metric.>` service metrics publishers beyond fleet container
  reports (Monitoring v2).
- Hub Sentinelle module (ADR-091 consumer: hot triggers + cold scans +
  Discord alerts) and host sensor timers.
- Langfuse drill-down (units present but disabled) and cross-NATS span
  nesting.

## Key invariants

- Every operational signal belongs to exactly one plane; a new plane requires
  a new ADR. Job lifecycle facts are never re-emitted as `factory.event.*`.
- Sensors and connectors publish facts only; policy (alerting, job dispatch)
  belongs to the hub.
- The dashboard is the sole human surface; engines stay headless and
  swappable behind it — adding an engine adds no new human UI.
- The dashboard BFF never subscribes to JetStream planes directly; it reads
  hub-owned read models via NATS RPC + SSE, and queries engines over
  authenticated HTTP.
- Spans carry timeline metadata only: blob refs instead of bytes, IDs instead
  of conversation text; span attributes are scrubbed at export and at ingest.
- Agent-turn tracing must come from Claude Code OTel — LiteLLM telemetry
  cannot see agent turns (agents bypass the proxy).
- No audit channel (operator JSONL, journald, rotation narrative) may ever
  contain secret bytes; audit writes are fail-soft.
- The PR-pipeline read model is non-authoritative — projection for
  visibility, never an input to merge or deploy decisions.
- M₁ deploy status is proven by fleet quorum against the published SHA; a
  converge certificate is metadata and never substitutes for fleet reports.
- Ingress publishes tenant only from registry resolution; unknown
  installations are dropped silently toward the caller (accepted response, no
  NATS publish, counted metric).
- Trace export is disable-able by config (`ROXABI_OTEL_ENABLED=0` →
  `NoopHooks`) — observability rollback must never require an image redeploy.

## See also

- Job plane & active-jobs registry → `docs/architecture/job-model.md`
- Domain security audit + ACL policy → `docs/architecture/security-routing.md`
- NATS planes & subject naming → `docs/architecture/messaging.md`
- Turn store & persistence surfaces → `docs/architecture/storage.md`
- Deploy topology & converge → `docs/architecture/deployment.md`
- Field runbooks → `docs/runbooks/operator-log.md`, `docs/runbooks/loki-query.md`, `docs/runbooks/otel-traces.md`, `docs/runbooks/ingress-webhooks.md`
- Operational quick reference → `docs/OBSERVABILITY.md` (redirect stub)

## ADR archive

| ADR | Title | Status |
|-----|-------|--------|
| 091 | Sentinelle — four observability planes | Accepted — 2026-06-25; design-only (consumer module not yet built); plane ① grammar amended by ADR-096 |
| 104 | Host presence from roxabi-sense | Proposed — 2026-08-01; factory owns contract/consumer; sense thin publisher; supersedes parallel host-sensor binary |
| 092 | Observability architecture — control-plane + headless engines | Accepted — amended 2026-07-04 (engines deployed); 2026-06-25 absorbs ADR-097 (trace plane v1 = otel-raw) |
| 093 | Operator audit — three-channel deploy logging | Accepted — 2026-06-26 |
| 094 | Control-plane dashboard consolidation | Accepted — 2026-06-27; BFF read path amended by ADR-097 |
| 096 | Ingress connector registry + tenant-scoped events | Accepted — 2026-06-29; amends ADR-091 plane ① for ingress subjects |
| 097 | OTel raw telemetry store (JSONL + SQLite, Langfuse deferred) | Superseded by ADR-092 §6 — archived (`adr/archive/`) |
| 098 | PR pipeline read model (dashboard pipeline panel) | Accepted — 2026-06-30; amends ADR-094 |
