---
title: Cross-project Contracts — Lyra
description: Living reference for the NATS SDK, shared schema package, voice/image worker contracts, and voice routing registry — the boundary between Lyra and the Roxabi satellite ecosystem.
---

# Cross-project Contracts — Lyra

> Status: LIVING — current truth for cross-project NATS contracts and shared schemas.
> Last updated: 2026-05-09.
> Source ADRs: 045, 049, 052. Absorbed: 037, 040, 044, 047, 050, 062, 066.

## Scope

This document defines what Lyra exports and imports across project boundaries. It covers three
coordinates: the shared NATS transport SDK (`roxabi-nats`), the shared Pydantic schema package
(`roxabi-contracts`), and the hub-side voice routing registry that determines how requests reach
satellite workers. Satellite projects (voiceCLI, imageCLI, roxabi-vault) consume these boundaries;
this document is the entry point for understanding what is stable, what is owned by whom, and
where the code lives.

## Current state

### roxabi-nats SDK (uv workspace)

`packages/roxabi-nats/` is a uv workspace subpackage colocated with the Lyra monorepo. It
provides the transport primitives satellites need to connect: `NatsAdapterBase`, `nats_connect`,
`circuit_breaker`, `readiness`, and serialization/sanitization helpers. Lyra consumes it via
`{ workspace = true }`; satellites consume it via `git + subdirectory=` against a pinned
`roxabi-nats/vX.Y.Z` tag.

The package is pure transport — zero knowledge of `lyra.*` subjects, envelope field names, or
domain semantics. It absorbed the outbound listener placement decision (ADR-037), the 9-finding
NATS architecture review (ADR-040), the connector ownership 7-rule table (ADR-047), and the ACL
inbox case normalization rollout (ADR-062). `CONTRACT_VERSION` migrated from `adapter_base.py` to
`roxabi_contracts.envelope`; a compat re-export remains through `roxabi-nats/v0.3.0`.

→ ADR-045

### roxabi-contracts (shared schemas)

`packages/roxabi-contracts/` is a second uv workspace subpackage. It ships Pydantic models,
subject string constants, synthetic test fixtures, and in-process test doubles for every
Lyra-owned cross-project NATS domain. Satellites import the same typed models the hub publishes
against — drift between publisher and subscriber becomes a type error, not a silent wire mismatch.

Live submodules as of v0.1.0+: `voice/` (TTS + STT subjects, models, fixtures, `FakeTtsWorker`/
`FakeSttWorker`), `image/` (generate + heartbeat subjects, 750 KB base64 ceiling, path
sanitization allowlist), `errors.py` (`WorkerError` unified error envelope with code namespace
registry, adopted on `TtsResponse`, `SttResponse`, `ImageResponse`, `LlmResponse`,
`CliChunkEvent`, `LlmChunkEvent`), plus `cli/`, `llm/`, `jobs/`, `gh/`, `audit/` submodules.

The `[testing]` extra is the only install path that pulls transport code; production installs have
zero `nats-py` dependency. Three production-contamination guards on test doubles: extras gate,
`LYRA_ENV` assertion, loopback-only NATS URL check. This package absorbed the voice contract
(ADR-044), the image contract (ADR-050), and the unified error envelope (ADR-066).

→ ADR-049

### Voice registry routing

`WorkerRegistry` (at `src/lyra/nats/worker_registry.py`) is the single routing truth for hub-side
voice clients (`NatsSttClient`, `NatsTtsClient`, `NatsImageClient`). Requests are published
directly to per-worker subjects (`lyra.voice.stt.request.<worker_id>`,
`lyra.voice.tts.request.<worker_id>`, `lyra.image.request.<worker_id>`). NATS queue-group
fallback is removed — it created dual-LB disagreement when heartbeat view and TCP subscription
view diverged.

On timeout or `NoRespondersError`, the client calls `mark_stale(worker_id)` and walks the next
candidate in `ordered_by_score()`. Circuit-breaker failure is recorded once per full walk
exhaustion, not per candidate, so a batch of stale workers does not prematurely open the breaker.
Workers are re-admitted automatically on their next heartbeat (heartbeat TTL: 15 s, cadence: 5 s).

→ ADR-052

## Key invariants

- All cross-project NATS schemas live in `packages/roxabi-contracts/`; none are re-implemented in satellite repos.
- All NATS transport primitives live in `packages/roxabi-nats/`; zero `lyra.*` subject knowledge permitted there.
- `roxabi-contracts` runtime has zero transport dependency — `nats-py` enters only via `[testing]` extra.
- Satellites MUST confine `import roxabi_contracts.<domain>` to a single designated adapter module per repo (grep-gate, ADR-047).
- Every new `lyra.<domain>.*` subject namespace requires a contract ADR before any satellite ships a worker.
- Hub-side clients MUST route exclusively via `WorkerRegistry.ordered_by_score()`; queue-group fallback is forbidden.
- Security-bearing fields (identity attestation, auth scopes) require a major `roxabi-contracts` bump + new `contract_version`; they are not eligible for additive introduction.
- External consumers pin `roxabi-nats` and `roxabi-contracts` by tag (`roxabi-nats/vX.Y.Z`); branch pinning is forbidden in `staging`/`main` of any production satellite.
- All binary fixtures in `roxabi-contracts` are synthetically generated (never from real user data or model outputs).
- `NatsAdapterBase._dispatch()` is the only caller of `deserialize()` in production paths; direct `Model.model_validate_json()` on raw `msg.data` is forbidden (bypasses 1 MB byte-size gate).

## Open questions / known gaps

- ADR-040 Finding 3 (open): `NatsBus` staging queue hardcoded to 500; `platform_queue_maxsize` config ignored — pre-dates ADR-065 JetStream adoption, not yet resolved.
- ADR-040 Finding 6 (open): `_get_hints` lacks a per-type cache; reflection runs on every deserialization.
- ADR-040 Finding 7 (open): outbound queue is unbounded; no max-age drain before circuit opens.
- `roxabi-nats/v0.3.0` will remove the `CONTRACT_VERSION` compat re-export from `adapter_base.py`; satellite imports of `roxabi_nats.CONTRACT_VERSION` will break and require a source update.
- VoiceCLI queue-group subscriptions are still present for fallback compatibility (ADR-052 follow-up); removal tracked but not yet landed.
- `lyra.memory.*` contract ADR not yet written; roxabi_contracts.memory submodule does not exist — `import roxabi_contracts.memory` would fail at import time.
- PyPI publication for both subpackages is deferred; triggers: ≥3 external consumers in `staging`, `contract_version: "2"`, or monorepo clone size becomes a friction point.
- `make test-acl` CI integration test (ADR-062 Fix 3) — required per ADR but track status separately.

## See also

- NATS subject naming → `messaging.md` (ADR-035)
- Per-identity inbox prefix → `security-routing.md` (ADR-051)
- ACL request/reply derivation → `security-routing.md` (ADR-064)

## ADR archive

| ADR | Title | Status |
|-----|-------|--------|
| 045 | Extract roxabi-nats SDK as uv workspace subpackage | Accepted |
| 049 | Extract roxabi-contracts as shared schema package | Accepted |
| 052 | Registry-authoritative voice routing | Accepted |
| 037 | NatsOutboundListener placement and adapter standalone bootstrap | Absorbed by ADR-045 |
| 040 | NATS messaging architecture review (9-finding table) | Absorbed by ADR-045 |
| 047 | NATS connector ownership pattern (7 rules + satellite grep-gate) | Absorbed by ADR-045 |
| 062 | NATS ACL inbox case normalization | Absorbed by ADR-045 |
| 044 | lyra ↔ voicecli NATS voice contract | Absorbed by ADR-049 |
| 050 | lyra ↔ imagecli NATS image contract | Absorbed by ADR-049 |
| 066 | Unified WorkerError envelope | Absorbed by ADR-049 |
