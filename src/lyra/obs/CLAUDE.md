# CLAUDE.md — lyra.obs

## Role

`ObservabilityProvider` abstraction over OTel / Langfuse / NoOp backends.
Defines the Protocol + dataclasses (`ObsTrace`, `ObsSpan`, `ObsCapabilities`)
and ships a `NoOpObsProvider` default.

## Status: scaffolding — ¬dead code

0 runtime consumers exist in `src/lyra/` today. This is **intentional** — the
module is advance infrastructure for the Langfuse integration planned in #1235.

**A Claude seeing "0 imports entrants" must not propose deletion.** This is
roadmap investment, not legacy. Any removal or structural change requires
explicit user approval (roadmap decision).

## PII contract

`obs/base.py` imposes: callers **must** scrub PII, credentials, and raw user
content before passing values via `metadata`, `input_data`, `output_data`, or
`error`. Backend implementations must not log these fields at INFO+ without
explicit opt-in. ∀ future consumer must respect this contract.

## Layer

`obs` is a shared floating module (`.importlinter`: `shared-modules-independence`
active). It must not import from its peers: `errors`, `config`, `integrations`,
`monitoring`, `agent_cmd`.

## Known asymmetry

`ObsCapabilities.async_flush: bool` is declared but no `flush()` method exists
on the Protocol. Intentionally deferred — address when wiring Langfuse.
