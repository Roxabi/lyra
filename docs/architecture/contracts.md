---
title: Cross-project Contracts — factory
description: Living reference for the shared workspace packages (roxabi-nats, roxabi-contracts, roxabi-satellite, roxabi-blobs, roxabi-obs, roxabi-otel), the hub transport layer, and voice routing/lifecycle — the boundary between the factory hub and the Roxabi satellite ecosystem.
---

# Cross-project Contracts — factory

> Status: LIVING — current truth for cross-project NATS contracts and shared packages.
> Last updated: 2026-07-02.
> Source ADRs: 045, 049, 052, 084, 095. Absorbed: 037, 040, 044, 047, 050, 062, 066.

## Scope

This document defines what factory exports and imports across project boundaries: the uv
workspace packages under `packages/`, the hub-side transport layer (`src/factory/transport/`),
and the registry + lifecycle machinery that routes requests to satellite workers. Satellite
projects (voiceCLI, imageCLI, llmCLI, cortex-memory — historically also roxabi-vault, now
deprecated and archived) consume these boundaries. Subject-plane semantics and hub dispatch live in
`messaging.md`; ACL policy lives in `security-routing.md`.

## Current state

### Workspace packages (inventory)

Every package below is a uv workspace subpackage colocated with the factory monorepo. The hub
consumes them via `{ workspace = true }`; external satellites consume them via
`git + subdirectory=` sources.

| Package | Role |
|---|---|
| `packages/roxabi-nats/` | NATS transport SDK — adapter/driver bases, connect, wire sanitization; test doubles behind `[testing]` |
| `packages/roxabi-contracts/` | Pydantic schemas + subject-string constants for every cross-project NATS domain |
| `packages/roxabi-satellite/` | Shared satellite plumbing — blob client, envelope coercion, token validation, error replies (#2024) |
| `packages/roxabi-blobs/` | Content-addressed BlobStore, FS + HTTP drivers (ADR-067 — see `storage.md`) |
| `packages/roxabi-obs/` | `FleetReporter` — periodic container reports on `factory.metric.host.container_report` |
| `packages/roxabi-otel/` | `OtelLifecycleHooks` + `scrub_attrs` — OTel span lifecycle for NATS workers |

### roxabi-nats SDK

`packages/roxabi-nats/` provides the transport primitives every NATS participant needs:
`NatsAdapterBase` (subscribe + envelope validation + lifecycle hooks), `NatsDriverBase`,
`nats_connect`, `sanitize_for_wire`, circuit-breaker and readiness helpers. The public API is
exactly the package `__all__`; `_`-prefixed submodules are hub-internal and may change without
notice — external consumers must not import them.

The package is pure transport: domain subject strings live in `roxabi_contracts`, not here.
The only subject literal the SDK owns is the startup announce subject `factory.system.ready`
(`readiness.py`). It absorbed the outbound-listener placement decision (ADR-037), the NATS
architecture review findings (ADR-040), the connector ownership rules (ADR-047), and the ACL
inbox case normalization rollout (ADR-062).

`CONTRACT_VERSION`'s canonical home is `roxabi_contracts.envelope`; `roxabi_nats` re-exports it
at top level, and access via `roxabi_nats.adapter_base` goes through a lazy shim that emits a
`DeprecationWarning`. Test doubles (`FakeTtsWorker`, `FakeSttWorker`, `FakeImageWorker`) live in
`roxabi_nats.testing` behind the `[testing]` extra — they moved here from `roxabi_contracts`
(ADR-059 follow-up), together with the production-contamination guards (`assert_not_production`
env assertion, `assert_loopback_url` NATS URL check); the old `roxabi_contracts` import paths
survive as deprecation shims.

→ ADR-045

### roxabi-contracts (shared schemas)

`packages/roxabi-contracts/` ships the typed wire contract: Pydantic models, frozen
`Literal`-typed `SUBJECTS` namespaces (a typo'd subject fails pyright, not production),
per-worker subject helpers (`per_worker_tts`, `per_worker_stt`, `per_worker_image`), synthetic
fixtures, and the shared envelope (`ContractEnvelope`, `WorkEnvelope`, `CONTRACT_VERSION`).
Satellites import the same models the hub publishes against — publisher/subscriber drift becomes
a type error, not a silent wire mismatch.

Domain submodules include `voice`, `image`, `cli`, `llm`, `jobs`, `gh`, `audit`, `state`,
`fleet`, `dashboard`, `turns`, `memory` (roxabi-cortex satellite, ADR-087 —
subjects under `roxabi.memory.*`), and siblings — the package tree is the inventory.
Notable recent moves:

- **CliPool subjects** moved into `roxabi_contracts.cli` (#2023): `factory.jobs.claude`,
  `factory.clipool.control`, `factory.clipool.heartbeat`, and the `clipool-workers` queue group
  are contracts constants; `factory.adapters.clipool` imports them rather than defining them.
- **Voice queue groups** aligned to contracts SSoT (#2026): `factory.nats.queue_groups` derives
  the TTS/STT worker queue-group names (`tts_workers`, `stt_workers`) from
  `roxabi_contracts.voice` `SUBJECTS` instead of local literals.
- **Voice lifecycle subjects** (ADR-095) live in `roxabi_contracts.voice` alongside the request
  and heartbeat subjects — see the voice lifecycle section below.

`errors.py` carries the unified `WorkerError` envelope (absorbed ADR-066) and the canonical
`KNOWN_CODES` registry, synced to `packages/roxabi-contracts/docs/error-codes.md` by a CI gate.
The runtime dependency set is Pydantic only — `nats-py` never enters via this package, not even
through its `[testing]` extra (transport-dependent testing moved to `roxabi-nats[testing]`).
External consumers pin both SDK packages by git tag (`roxabi-nats/vX.Y.Z`) with a grouped
Renovate rule so transport and schemas upgrade in lockstep; branch pinning these two is forbidden
in production satellites. The one sanctioned branch pin — the `roxabi-satellite` aggregator — and
its rationale are in the Pin doctrine section below.

→ ADR-049

### roxabi-satellite (satellite plumbing)

`packages/roxabi-satellite/` (#2024) factors the factory-NATS plumbing that every GPU worker CLI
was re-implementing: `HttpBlobStore` singleton wiring (`roxabi_satellite.blobs`, ADR-068 env
config), `coerce_envelope_fields` for hub payloads, `validate_nats_token` (public token
validation with no private SDK imports), `resolve_worker_error` registries, and per-domain
ingress validation + wire-safe error reply builders (`voice`, `image`, `llm`
submodules). It is **not** a home for domain engines — Whisper/TTS/diffusion/LiteLLM logic stays
in each CLI; adapters and runners stay in the satellite repos. It pulls `roxabi-contracts`,
`roxabi-nats`, and `roxabi-blobs` transitively so hub and satellites validate ingress with the
same code. (Socialmedia NATS satellite removed #2329.)

### Transport layer

The transport layer (`src/factory/transport/`, Epic #1277/#1278) provides the domain-agnostic
primitives all hub-side domain worker clients compose. It is the hub-internal counterpart of the
roxabi-nats SDK (ADR-045 lineage).

**Three-layer composition:**

```
NatsTransport           — call() / publish() / open_inbox()
      │
WorkerPoolClient        — routing + circuit-breaker + heartbeat subscription
      │
DomainClient            — thin wrapper in factory.nats / factory.llm
                          (LlmClient, NatsSttClient, NatsTtsClient, NatsImageClient)
```

**`NatsTransport`** (`transport/nats_request_response.py`) owns all NATS-specific I/O:

| Method | Description |
|--------|-------------|
| `call(subject, payload, *, timeout)` | Request-reply; returns `Result[bytes, SanitizedError]` |
| `publish(subject, payload, *, reply_subject)` | Fire-and-forget publish with explicit reply subject |
| `open_inbox()` | Async context manager yielding `InboxStream`; inbox valid only inside the CM |

**`WorkerPoolClient`** (`transport/worker_pool_client.py`) composes a transport plus an injected
`WorkerRegistry` and owns: `request_with_routing(subject_fn, payload)` (iterates scored workers,
calls `transport.call(subject_fn(worker_id), payload)`, applies the circuit-breaker, marks stale
workers on timeout/no-responders), `stream_request(subject, payload)` (opens the inbox CM,
publishes, iterates chunks), and `start(nc)` / `stop()` (heartbeat subscription lifecycle).

**Domain clients** (`factory.nats.*`, `factory.llm.llm_client`) are thin wrappers composing a
`WorkerPoolClient` with a codec. They must not add a second circuit-breaker.

**Typed boundary.** All transport-level methods return `Result[T] = Ok[T] | Err[SanitizedError]`
— no exceptions cross the transport boundary. `SanitizedError` (`transport/_result.py`) carries
a stable `code` string (e.g. `transport.timeout`, `transport.no_responders`,
`pool.circuit_open`) and a `message` containing `type(exc).__name__` only — never `str(exc)` —
so no internal detail leaks to users or logs.

**`HttpTransport`** (`transport/http_transport.py`) structurally satisfies the transport
protocol as a skeleton for future HTTP-backed LLM providers: `call` returns a `NOT_WIRED`
`Err`, while `publish` and `open_inbox` raise `NotImplementedError` (HTTP streaming is
codec-level SSE, not transport-level). Domain clients would compose it as
`LlmClient(pool=WorkerPoolClient(HttpTransport(...)), codec=...)` once wired.

### Voice routing registry

`WorkerRegistry` (`src/factory/nats/worker_registry.py`) is the single routing truth for
hub-side worker clients; bootstrap injects it into each `WorkerPoolClient`. Requests are
published directly to per-worker subjects built by the contracts helpers
(`factory.voice.stt.request.{worker_id}`, `factory.voice.tts.request.{worker_id}`, and the image
equivalent). NATS queue-group load balancing is not used for hub routing — it created dual-LB
disagreement when the heartbeat view and the TCP subscription view diverged.

On timeout or no-responders the pool marks the worker stale (`mark_stale`) and walks the next
candidate from `ordered_by_score()`. Circuit-breaker failure is recorded once per full walk
exhaustion, not per candidate, so a batch of stale workers does not prematurely open the
breaker. Workers are re-admitted on their next heartbeat (`record_heartbeat`, which also
validates the worker's `nats_token`); liveness expires on a heartbeat TTL (default 15 s).

→ ADR-052

### Voice lifecycle and capabilities

ADR-095 splits worker **health** (heartbeat subjects, registry liveness) from **catalogue
discovery**: `factory.voice.tts.lifecycle.list` / `factory.voice.tts.lifecycle.status` and the
STT equivalents are request-reply subjects answered by the voice workers, carrying
`VoiceLifecycleRequest` / `VoiceLifecycleResponse` (`roxabi_contracts.voice`). voiceCLI owns the
engine + sample catalogue SSoT; the hub maps agents to a `sample_id`. Hub side,
`VoiceLifecycleClient` (`src/factory/nats/voice/voice_lifecycle_client.py`) performs the
request-reply; the dashboard BFF aggregates catalogues over
`factory.dashboard.voice.capabilities` (`roxabi_contracts.dashboard`).

→ ADR-095

### Required field additions (wire-breaking without a shim)

Optional/non-security fields follow ADR-049's minor path (`extra="ignore"` on consumers) — no
shim sequence. This section covers **semantically required** fields only.

A **required** field added to a `roxabi-contracts` model is wire-breaking even when the change
looks purely additive, for two independent reasons:

1. **Satellite lag.** llmCLI/voiceCLI/imageCLI are lock-pinned on older `roxabi-contracts` SHAs
   and keep producing payloads without the new field until they bump their lock — hub-side
   deserialization of those payloads would raise.
2. **JetStream replay.** Persisted messages (e.g. `TurnWriteEvent` on stream `FACTORY_TURNS`)
   replay old payloads to new consumers across a deploy; M₁ hub uptake follows the next converge
   after `staging` merge (typically ≤5–10 min via podman-auto-update), not the next satellite
   release.

**Ineligible:** security-bearing fields (identity attestation, auth scopes, signed tokens, audit
provenance) — major `roxabi-contracts` bump + coordinated satellite upgrade only (ADR-049
§Versioning). No `default_factory` shim.

**Pattern:** land the field as a `default_factory` mint shim (deserialize-compat within the current
`CONTRACT_VERSION`, transitional per ADR-084 Amendment / #1619), have every in-repo producer set
it explicitly at each construction site (+ fakes/fixtures/docker stubs), and let satellites echo
it after their next Renovate `roxabi sdk` lock bump (`packages/roxabi-contracts/README.md`
§ Satellite pin freshness — human-gated PR when `CONTRACT_VERSION` changes).
Flip the field to hard-required only on the next `CONTRACT_VERSION` bump (#1841 for `job_id`).
Worked example: `WorkEnvelope.job_id` (#1619, ADR-084 Amendment).

**How to apply:** before choosing required vs. default for any field addition/requirement
change, enumerate every producer per direction (in-repo vs. satellite), every stream-persisted
model that carries that field, and extend subject→envelope enforcement tests when adding
work-plane fields. This enumeration is the actual gate — spec review alone has missed it before.

## Pin doctrine (external consumers)

One doctrine, one documented exception. This section is the SSoT; the package
READMEs and the invariant below point here rather than restating it.

**Default — pin the wire-contract SDK by tag.** External consumers pin
`roxabi-nats` and `roxabi-contracts` by git **tag** (`roxabi-nats/vX.Y.Z`,
`roxabi-contracts/vX.Y.Z`), grouped in a single Renovate `git-refs` rule
(`groupName: "roxabi sdk"`) so transport and schemas move in lockstep. Branch
pinning these two is **forbidden** in `staging`/`main` of any production
satellite (permitted only in plugin-dev branches, per ADR-045). Rationale: a
branch pin lets the wire `CONTRACT_VERSION` shift under a consumer with no
coordinated version bump, and ungrouped tag pins can drift into a **partial
upgrade** (schemas bumped, transport stale) that fails at envelope-parse time —
the grouped tag rule is what prevents both.

**Exception — the `roxabi-satellite` aggregator may pin `branch = "staging"`.**
A CLI that consumes plumbing depends on `roxabi-satellite` *only* and receives
`roxabi-contracts`, `roxabi-nats`, and `roxabi-blobs` transitively. This is
sanctioned because:

- `roxabi-satellite` is **plumbing, not the wire contract** — its surface is
  validation/replies/blob-token helpers, not the `CONTRACT_VERSION` boundary.
- Branch-tracking the aggregator **cannot produce a partial upgrade** — the
  hazard the tag rule exists to prevent. All three SDK packages resolve from
  the *same* staging commit (workspace deps), so the consumer always gets an
  internally-consistent triple, never a schemas-ahead-of-transport skew.
- `roxabi-satellite` (and `roxabi-blobs`) are pre-1.0 and **not yet
  tag-released** (no `roxabi-satellite/*` tags exist); branch-tracking lets
  plumbing fixes propagate without a manual repin per ADR-082.

This is a bounded carve-out: once `roxabi-satellite` starts cutting tags it
folds back into the default tag rule. Consumers must never branch-pin
`roxabi-nats`/`roxabi-contracts` **directly** to dodge the tag rule — only the
aggregator is branch-pinnable.

**Release reminder (no gate).** Every `version =` bump in either wire-contract
package's `pyproject.toml` MUST be followed by cutting the matching
`<pkg>/vX.Y.Z` tag in the same release (post-merge on `staging`, or via
`/promote`). A pyproject that is ahead of the newest tag is exactly what breaks
external `uv add` resolution (issue #2199). This is a **release-runbook
reminder, not a quality gate**: tags are cut post-merge, so at PR time the
pyproject is legitimately ahead of the tag — a `version == latest-tag` gate
would red every version-bump PR, which is the wrong shape. The proportionate
control is this line plus the [Unreleased] "tag pending" note each package's
CHANGELOG carries until its tag is cut.

## Key invariants

- All cross-project NATS schemas and subject strings live in `packages/roxabi-contracts/`; none are re-implemented in satellite repos, and subjects are frozen `Literal`-typed constants (no inline f-string subject construction outside the contracts per-worker helpers).
- `packages/roxabi-nats/` stays domain-agnostic: no domain subject literals or envelope field names beyond the readiness announce subject; only `__all__` names are stable external API.
- `roxabi-contracts` runtime has zero transport dependency; transport-coupled test doubles enter only via `roxabi-nats[testing]`.
- Test doubles are guarded against production contamination: extras gate, `assert_not_production` environment assertion, `assert_loopback_url` NATS URL check.
- Satellite domain engines stay in satellite repos; only plumbing (validation, replies, blob/token/envelope helpers) may move into `roxabi-satellite`.
- External consumers pin `roxabi-nats` and `roxabi-contracts` by tag with the grouped Renovate rule; branch pinning these two is forbidden in `staging`/`main` of any production satellite. The sole exception — branch-pinning the `roxabi-satellite` aggregator — is defined in the Pin doctrine section (partial-upgrade-safe, pre-tag plumbing).
- Hub-side clients route exclusively via registry-scored per-worker subjects through `WorkerPoolClient`; queue-group fallback routing is forbidden.
- No exceptions cross the transport boundary: transport methods return `Result`, and `SanitizedError.message` carries the exception type name only, never `str(exc)`.
- Domain clients composing `WorkerPoolClient` must not add a second circuit-breaker.
- Security-bearing fields (identity attestation, auth scopes) require a major `roxabi-contracts` bump + new `CONTRACT_VERSION`; they are not eligible for additive introduction.
- All binary fixtures in `roxabi-contracts` are synthetically generated — never from real user data or model outputs.
- Every new `factory.<domain>.*` cross-project subject namespace requires a contract ADR before any satellite ships a worker.
- A required field added to any model MUST go through the default-mint shim → producer enumeration → `CONTRACT_VERSION` bump sequence (see "Wire-compatible field additions" above); shipping a bare required field is a wire-breaking change, not an additive one.

## Open questions / known gaps

- ADR-040 Finding 3 (open): `NatsBus` staging queue hardcoded to 500; `platform_queue_maxsize` config ignored — pre-dates ADR-065 JetStream adoption, not yet resolved.
- ADR-040 Finding 6 (open): `_get_hints` lacks a per-type cache; reflection runs on every deserialization.
- ADR-040 Finding 7 (open): outbound queue is unbounded; no max-age drain before circuit opens.
- VoiceCLI queue-group subscriptions are still present for fallback compatibility (ADR-052 follow-up); removal tracked but not yet landed.
- `lyra.memory.*` contract ADR not yet written; roxabi_contracts.memory submodule does not exist — `import roxabi_contracts.memory` would fail at import time.
- PyPI publication for both subpackages is deferred; triggers: ≥3 external consumers in `staging`, `contract_version: "2"`, or monorepo clone size becomes a friction point.
- `make test-acl` CI integration test (ADR-062 Fix 3) — required per ADR but track status separately.

## See also

- NATS planes, subject naming, chunk protocol → `messaging.md` (ADR-035, 036, 065, 076)
- ACLs, request/reply derivation, inbox prefix → `security-routing.md` (ADR-046, 051, 064)
- BlobStore architecture (roxabi-blobs consumer side) → `storage.md` (ADR-067, 068)
- Worker runtimes and job dispatch → `workers-tooling.md` and `job-model.md`

## ADR archive

| ADR | Title | Status |
|-----|-------|--------|
| 045 | Extract roxabi-nats SDK as uv workspace subpackage | Accepted |
| 049 | Extract roxabi-contracts as shared schema package | Amended |
| 052 | Registry-authoritative voice routing | Amended |
| 084 | WorkEnvelope — the job_id invariant | Amended — current truth in `job-model.md` |
| 095 | Voice lifecycle plane — heartbeat vs capabilities listing | Superseded — archived (invariants live in `messaging.md` § Voice lifecycle) |
| 037 | NatsOutboundListener placement and adapter standalone bootstrap | Absorbed by ADR-045 |
| 040 | NATS messaging architecture review | Absorbed by ADR-045 |
| 047 | NATS connector ownership pattern | Absorbed by ADR-045 |
| 062 | NATS ACL inbox case normalization | Absorbed by ADR-045 |
| 044 | Voice NATS contract (historically lyra ↔ voicecli) | Absorbed by ADR-049 |
| 050 | Image NATS contract (historically lyra ↔ imagecli) | Absorbed by ADR-049 |
| 066 | Unified WorkerError envelope | Absorbed by ADR-049 |
