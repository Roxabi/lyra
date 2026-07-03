# AGENTS.md — factory.obs

## Role

Two concerns share this module:

1. **Live OTel tracing** (`hub_tracer.py`, `otel_wiring.py`, `otlp_export.py`) —
   wired into the runtime today. `hub_tracer` mints ingress-turn and NATS-client
   spans; `otel_wiring.build_lifecycle_hooks()` is the composition root for NATS
   worker OTel hooks (`roxabi-otel`); `otlp_export` supplies the shared OTLP gRPC
   endpoint + bearer wiring that ships spans to `factory-otel`. Gated on
   `ROXABI_OTEL_ENABLED` — falls back to noop hooks / no exporter when disabled.
   This is plane ① of ADR-091 (four observability planes); implementation #2069.
2. **`ObservabilityProvider` abstraction** (`base.py`, `noop.py`) — the
   Langfuse-shaped Protocol + dataclasses (`ObsTrace`, `ObsSpan`,
   `ObsCapabilities`, `GenerationKwargs`) with a `NoOpObsProvider` default. Still
   forward-facing: no runtime consumer wires a non-noop provider yet — the
   dashboard/Langfuse composition is planned in #1235. Do not delete it as
   "dead"; it is the seam that integration lands on.

## Runtime consumers (tracing)

`hub_tracer` / `otel_wiring` are imported by runtime prod code — grep before
assuming otherwise:

```
git grep -l "from factory.obs" -- 'src/**/*.py' | grep -v 'src/factory/obs/'
```

Live callers include hub middleware (`core/hub/middleware/middleware_guards.py`),
the ingress orchestrator, the LLM RPC drivers (`llm/drivers/claude_rpc.py`,
`omp_rpc.py`), the transport worker-pool client, and the worker/clipool
bootstraps. A change to the `*_span` signatures or hook wiring fans out to all of
them.

## PII contract

`obs/base.py` imposes: callers **must** scrub PII, credentials, and raw user
content before passing values via `metadata`, `input_data`, `output_data`, or
`error`. Backend implementations must not log these fields at INFO+ without
explicit opt-in. ∀ provider (live exporter or future Langfuse) must respect this
contract; spans emitted by `hub_tracer` carry only sanitized envelope ids
(`trace_id`, `job_id`, subject, component) per `roxabi_contracts.telemetry`.

## Layer

`obs` is a shared floating module (`.importlinter`: `shared-modules-independence`
active). It must not import from its peers: `errors`, `config`, `integrations`,
`monitoring`, `agent_cmd`, nor from `bootstrap`/`adapters`/`infrastructure`
(`shared-modules-upper-boundary`).

## Known asymmetry

`ObsCapabilities.async_flush: bool` is declared but no `flush()` method exists on
the `ObservabilityProvider` Protocol. Intentionally deferred — address when
wiring the non-noop provider (#1235).
