---
id: importlinter-adr048-transition
slug: importlinter-adr048-transition
title: Downward TYPE_CHECKING imports (ADR-048 transition)
status: open
created: 2026-05-11
drain_slice: "#1163"
parent_slice: '#1162'
rule: importlinter
rules:
  - importlinter
sites: see artifacts/quality-debt-report.json
fix_class: medium
---

# importlinter-adr048-transition

## Pattern

The `clean-architecture-layers` and `agents-no-bootstrap` importlinter contracts enforce a strict upward dependency flow: `core` must not import from `infrastructure`. However, 22 `ignore_imports` entries currently waive this constraint by permitting downward `TYPE_CHECKING` imports from `lyra.core.*` modules into `lyra.infrastructure.stores.*`.

These waivers exist because the store types (AgentStore, TurnStore, PairingStore, MessageIndex, etc.) were migrated from `lyra.core.stores` to `lyra.infrastructure.stores` under ADR-048 (issue #760, reviewed in #935). Modules in `lyra.core` that previously imported store types from a sibling package now cross a layer boundary. The imports are guarded by `TYPE_CHECKING`, so they carry no runtime cost and cause no circular import errors — but they remain architectural violations that importlinter cannot dismiss without an `ignore_imports` entry.

This is tagged DEBT rather than POLICY because the correct resolution is protocol extraction (ADR-059): each store's interface should be declared as a `Protocol` in `lyra.core.stores`, allowing `lyra.core.*` modules to depend on the protocol rather than the concrete infrastructure class. Each `ignore_imports` entry is then a concrete drain target — removed when its corresponding protocol is extracted.

## Sites

See `artifacts/quality-debt-report.json` stale_references for the full list (22 entries). Representative sites from `.importlinter` `[importlinter:contract:clean-architecture-layers]` and `[importlinter:contract:agents-no-bootstrap]`:

- `.importlinter:17` — `lyra.core.agent.agent_refiner -> lyra.infrastructure.stores.agent_store`
- `.importlinter:34` — `lyra.core.hub.hub -> lyra.infrastructure.stores.turn_store` (TurnStore moved per ADR-048 #760 review fix f2)
- `.importlinter:32` — `lyra.core.stores.pairing_protocol -> lyra.infrastructure.stores.pairing` (ADR-059 V3 facade pending)
- `.importlinter:70` — `lyra.agents.simple_agent -> lyra.infrastructure.stores.agent_store` (ADR-059 V9 protocol extraction pending)
- `.importlinter:25` — `lyra.core.hub.hub -> lyra.infrastructure.stores.message_index` (migrated store #935)

## Drain plan

- For each `ignore_imports` entry in `.importlinter`, identify the store interface being imported.
- Declare a `Protocol` for that interface in `lyra.core.stores` (following the ADR-059 protocol extraction pattern).
- Update the importing module to depend on the `Protocol` (in `lyra.core.stores`) rather than the concrete class (in `lyra.infrastructure.stores`).
- Remove the corresponding `ignore_imports` line from `.importlinter`.
- Each extraction can land as an independent PR; drain is incremental per-store.
- Priority order (most-referenced first): `TurnStore` (6 waivers), `MessageIndex` (4 waivers), `AgentStore` (3 waivers), `IdentityAliasStore` (3 waivers), `Pairing` (2 waivers), `PairingProtocol` (1 waiver), `AuthStore` (1 waiver), `PrefsStore` (1 waiver).
- `agents-no-bootstrap` entry (`simple_agent -> agent_store`) drains when ADR-059 V9 protocol extraction lands.
- Some entries (e.g., `pairing_protocol` facade) may require ADR-059 V3 design review before protocol shape is settled; defer those until V3 is merged.

## Notes

- ADR-048: store migration from `lyra.core.stores` to `lyra.infrastructure.stores`. Original migration PR #760, follow-up store additions #935.
- ADR-059: protocol extraction strategy for bridging the layer gap. V3 covers pairing; V9 covers agent store.
- Issue #977: related — shared-modules independence false positives (see `importlinter-shared-modules-transitive`).
- These waivers were added incrementally as stores were moved; no single commit introduced all 22. Each waiver has a comment in `.importlinter` referencing the originating issue.
- Lifecycle: this slug drains to `status: drained` when all 22 `ignore_imports` entries are removed from `.importlinter`. The registry file is retained as a no-reintroduction sentinel.
- Related registry: [`importlinter-shared-modules-transitive.md`](importlinter-shared-modules-transitive.md)
