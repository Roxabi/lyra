---
id: importlinter-shared-modules-transitive
slug: importlinter-shared-modules-transitive
title: Transitive paths through lyra.core (independence contract)
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

# importlinter-shared-modules-transitive

## Pattern

The `shared-modules-independence` contract in `.importlinter` declares that a set of "floating" modules (`lyra.obs`, `lyra.stt`, `lyra.tts`, `lyra.errors`, `lyra.config`, `lyra.integrations`, `lyra.monitoring`, `lyra.agent_cmd`) must be mutually independent — no peer imports each other. This is a structural invariant: these modules are utilities or adapters that should not form a dependency graph among themselves.

Importlinter's `independence` contract type traces *all* paths between listed modules, including transitive paths through unlisted intermediaries. Four `ignore_imports` entries exist because importlinter flags pairs of modules that share a path through `lyra.core` — for example, `lyra.tts.engine_selector` imports from `lyra.core.agent.agent_config`, and `lyra.core.agent.agent` imports from `lyra.tts`. Importlinter interprets this as `tts ↔ tts` (self-loop via core) or as indirect coupling between two shared modules through `lyra.core`.

These paths are not actual peer coupling — they are independent permitted imports of the form `floating → core`. Importlinter's independence contract semantics do not distinguish "A imports core, B imports core" (permitted) from "A imports core, core imports B" (structural coupling). The waivers suppress false-positive detections that arise from this limitation, tracked in issue #977.

This is tagged DEBT rather than POLICY because the root cause is a contract-semantics mismatch, not a deliberate design choice. The long-term resolution is either to narrow the contract (exclude `lyra.core` as an allowed transit) or to accept these entries permanently and reclassify to POLICY if importlinter never adds the needed semantics.

## Sites

See `artifacts/quality-debt-report.json` stale_references for all 4 entries. From `.importlinter` `[importlinter:contract:shared-modules-independence]`:

- `.importlinter:89` — `lyra.tts.engine_selector -> lyra.core.agent.agent_config` (tts imports core config; flagged as peer coupling)
- `.importlinter:90` — `lyra.core.processors.processor_registry -> lyra.integrations.base` (core imports integrations; flagged as reverse path)
- `.importlinter:91` — `lyra.core.agent.agent -> lyra.stt` (core imports stt; flagged as reverse transitive)
- `.importlinter:92` — `lyra.core.agent.agent -> lyra.tts` (core imports tts; flagged as reverse transitive)

## Drain plan

- Option A (preferred if importlinter adds contract semantics): Update the `shared-modules-independence` contract to specify `lyra.core` as an allowed transit module (if importlinter supports this). Remove the 4 `ignore_imports` entries and let the contract enforce true peer isolation without false positives. Monitor importlinter releases for `independence` contract improvements.
- Option B (permanent POLICY reclassification): If importlinter semantics do not change, evaluate whether these 4 entries should be reclassified as `POLICY:importlinter-false-positive` with a vocabulary entry in `docs/quality-policy.md`. This would close the DEBT slug and remove the need for a registry file. Decision deferred to P2a drain pass review.
- Option C (restructure): Move `lyra.core.agent.agent_config` out of `lyra.core` into a shared-floating location, so `lyra.tts.engine_selector` importing it no longer crosses the boundary that triggers the independence check. High refactor cost; only worthwhile if ADR-048/059 work creates a natural opportunity.
- Do not remove these entries without verifying the independence contract still passes; they suppress real importlinter errors (even if the errors are false positives).

## Notes

- Issue #977: original ticket documenting the false-positive discovery for the independence contract transitive path detection.
- These 4 entries were added together as a batch waiver when #977 was investigated. No individual entry has a separate tracking issue.
- Relationship to `importlinter-adr048-transition`: both slugs cover importlinter waivers, but for different contracts and different root causes. The ADR-048 transition debt is a genuine architectural violation (wrong layer direction); this slug is a contract-semantics limitation.
- Lifecycle: this slug drains to `status: drained` when either (a) all 4 `ignore_imports` entries are removed after importlinter semantics improve, or (b) the entries are reclassified to POLICY and the tag on `.importlinter` is updated to `POLICY:importlinter-false-positive`. In case (b), this registry file is marked `drained` and a POLICY vocabulary row is added to `docs/quality-policy.md`.
- Related registry: [`importlinter-adr048-transition.md`](importlinter-adr048-transition.md)
