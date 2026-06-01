# CLAUDE.md — roxabi-contracts

## Role

`roxabi-contracts` is the **single source of truth** for all cross-service NATS
message schemas in the Roxabi ecosystem. Lyra publishers and satellite
subscribers (voiceCLI, imageCLI, roxabi-vault, future services) import the same
typed Pydantic v2 models — drift between producer and consumer becomes a
type-check error, not a silent wire mismatch.

→ ADR-049 (`docs/architecture/adr/049-roxabi-contracts-shared-schema-package.mdx`)

## Boundary with `roxabi-nats`

| Package | Owns |
|---|---|
| `roxabi-contracts` | Schema definitions, subject constants, fixtures, test doubles |
| `roxabi-nats` | Wire transport — connect, publish, subscribe, deserialize |

`roxabi-contracts` has **zero runtime transport dependency**. `nats-py` and
`roxabi-nats` appear only in `[project.optional-dependencies].testing`. A
satellite that needs only schemas installs this package without pulling any
transport code.

## Serialization framework

**Pydantic v2** (`pydantic>=2`). All domain models subclass `ContractEnvelope`
(defined in `envelope.py`), which sets `model_config = ConfigDict(extra="ignore")`
as a forward-compat invariant — unknown fields are silently dropped, not errors.

Consumers MUST deserialize via `roxabi_nats.deserialize()`, NOT by calling
`Model.model_validate_json()` directly on raw `msg.data`. Direct Pydantic calls
bypass the 1 MB byte-size gate in the transport layer.

## Versioning and breaking-change rules

Tag scheme: `roxabi-contracts/v{major}.{minor}.{patch}` (independent of lyra
and roxabi-nats versions).

**Additive-only** (minor bump): add optional fields, add new domain submodules,
add new subjects to an existing domain.

**Breaking change** (major bump + new `contract_version` in the envelope):
rename or retype required fields, remove deprecated fields, or introduce any
**security-bearing field** (auth tokens, caller identity, audit provenance).
Security-bearing fields are NEVER eligible for additive introduction — a
consumer silently ignoring an `auth_token` is an exploitable bug, not a
forward-compat win. Breaking changes require an ADR + coordinated satellite
upgrade plan.

**Deprecation cycle:** mark field `deprecated=True` in Pydantic metadata for at
least one minor release before removal; announce in CHANGELOG.md.

## Module layout

Domain-grouped contract modules (run `ls src/roxabi_contracts/` for the full listing):

- **Shared primitives:** `envelope.py` (ContractEnvelope base), `errors.py` (WorkerError + KNOWN_CODES registry, ADR-066 (absorbed into ADR-049)), `blob_errors.py` (BlobNotFoundError, ADR-082), `blob_ref.py` (wire-side BlobRef, ADR-067), `_testing_guards.py`, `_nats_utils.py`
- **Integration contracts:** `voice/` (ADR-044 (absorbed into ADR-049)), `image/` (ADR-050 (absorbed into ADR-049)), `turns/` (#1331), `outbound/`, `event/`, `cli/`, `llm/`, `jobs/`, `gh/`, `audit/`
- **Sentinel:** `verify/` — ACL-verification deny-probe (ungranted, #1545)

Each domain submodule exposes: `SUBJECTS` (subject constants), models, and
optionally `fixtures` (pure synthetic data) and `testing` (test doubles —
requires `[testing]` extra).

## Consumer expectations

**Lyra hub/adapters** (`src/lyra/nats/`) — workspace dependency, uses
`[testing]` extra in dev/CI.

**Satellite services** (voiceCLI, imageCLI, roxabi-vault, future) — pin by git
tag; group `roxabi-contracts` and `roxabi-nats` in a single Renovate rule
(`matchDatasources: ["git-refs"]`) to prevent partial upgrades.

New domains land as new submodules + a minor version tag. Placeholder
directories MUST NOT be created — an empty module lets `import
roxabi_contracts.<domain>` succeed silently with missing attributes.

## turns/ (#1331)

`roxabi_contracts.turns` — TurnWriteEvent discriminated union (5 payload
kinds) + `SUBJECTS.turn_write = "lyra.turns.write"`. Consumed by
`lyra.transport.turn_publisher` (publishers) and
`lyra.infrastructure.turn_writer` (subscriber-writer).
