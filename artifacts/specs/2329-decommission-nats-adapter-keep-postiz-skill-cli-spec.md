---
title: "chore(socialmedia): decommission NATS adapter; keep Postiz + skill/CLI"
description: "Retire factory socialmedia NATS tool plane; keep Postiz + skill/CLI agent path"
type: spec
status: approved
issue: 2329
tier: F-lite
---

## Context

- **Source:** `artifacts/frames/2329-decommission-nats-adapter-keep-postiz-skill-cli-frame.md` (approved, F-lite)
- **Issue:** [#2329](https://github.com/Roxabi/roxabi-factory/issues/2329) (reframed: decommission, not HTTPS cutover)
- **Admission:** #2326 / `docs/architecture/workers-tooling.md` Rule 2 + fleet map
- **Related:** #1713 (satellite graduation tracker — supersede toward remove)

Inventory note: `[component.socialmedia-adapter]` is already **`disabled = true`** in `deploy/quadlet.toml` (WIP comment #1713). Code, ACL identity, hub client bootstrap, contracts, and docs still present.

## Intent

Remove an unused NATS satellite bridge that only proxies Postiz HTTP. The hub client is wire-only (no product caller). Agents already use skill/CLI → Postiz Public API. Keeping family-A process surface (nkey, ACL, heartbeat subject, code) without consumers violates capability admission.

## Goal

No socialmedia NATS tool plane remains in factory (process, ACL, hub wiring, adapter code); Postiz stays; agent publish path documented as skill/CLI.

## Users

- **Primary:** operators / fleet owners (less process surface)
- **Secondary:** agents using Postiz skill/CLI (unchanged path)

## Expected Behavior

1. Operator runs factory fleet: **no** `factory-socialmedia-adapter` unit/component, **no** socialmedia NATS identity grants.
2. Hub boots without `NatsSocialMediaClient` / socialmedia heartbeat subscription noise.
3. Agent wants to publish: uses Postiz skill + `~/.roxabi/postiz/bin/postiz` (or UI) against Postiz Public API — not NATS.
4. Docs (`workers-tooling` fleet map) state socialmedia-adapter **removed** with rationale; agent path pointed to skill/CLI.
5. #1713 closed or commented as superseded by decommission.

## Data Model & Consumers

### Wire (to remove)

| Artifact | Role today | After |
|---|---|---|
| `factory.tool.socialmedia.>` | request/reply subjects | **gone** from ACL + hub grants |
| `factory.tool.socialmedia.heartbeat` | satellite HB | **gone** |
| `roxabi_contracts.socialmedia` | models + SUBJECTS | **remove** if no remaining speakers (factory-only today) |
| `SocialMediaClientProtocol` | hub driven port | **remove** |
| Hub `_socialmedia_client` | set at bootstrap, never called | **remove** |

### Consumers

| Consumer | Fields / surface | Status |
|---|---|---|
| Hub product path | publish/list/schedule | **none today** — safe delete |
| `factory-socialmedia-adapter` | full adapter | delete (already disabled in Quadlet) |
| Agent skill/CLI | Postiz HTTP, not NATS | **keep** (outside this repo’s runtime) |
| Future hub HTTP client | — | **out of scope** |

### Secrets / deploy

| Item | Action |
|---|---|
| `factory-nats-socialmedia` | drop from secrets-policy + nkey inventory after ACL regen |
| `factory-socialmedia-api-key` | drop factory secret if only used by adapter; Postiz token for CLI stays under `~/.roxabi/postiz` |
| `postiz.network` Quadlet network | remove if only dual-home for adapter; Postiz stack owns its own networking |
| `[component.socialmedia-adapter]` | delete section (not merely leave disabled) |
| `auth.conf` | regenerate from matrix (`factory-acl genkeys` / project script) — do not hand-edit |

## Breadboard

| ID | Affordance | Handler / owner | Data / side-effect |
|---|---|---|---|
| N1 | Delete adapter package | `src/factory/adapters/socialmedia/**` | no process entry |
| N2 | Delete hub NATS client | `src/factory/nats/socialmedia/**` | no `factory.tool.socialmedia.*` requests |
| N3 | Unwire bootstrap | `voice_overlay.init_nats_socialmedia`, hub_assembly, hub_core, wiring_helpers, unified stop, `bootstrap/types` | hub no socialmedia client |
| N4 | Remove hub port | `core/ports/socialmedia.py`, `hub_registration.set_socialmedia_client` | port gone |
| N5 | Remove CLI entry | `cli/main.py` `socialmedia-adapter` command | `factory socialmedia-adapter` absent |
| N6 | Fleet catalog | `nats/fleet_catalog.py` | identity name gone |
| N7 | ACL matrix | `deploy/nats/acl-matrix.json` + fixtures | identity + RR flow gone; hub grants drop subjects |
| N8 | Secrets policy | `deploy/secrets-policy.toml` | nkey + api-key factory secrets gone |
| N9 | Quadlet | component section, `.container`, `postiz.network` if unused | no install path |
| N10 | Satellite helpers | `packages/roxabi-satellite/.../socialmedia` + tests | remove with adapter |
| N11 | Contracts | `packages/roxabi-contracts/.../socialmedia` + tests | remove if sole speaker |
| N12 | Tests | adapter/nats/bootstrap tests referencing socialmedia | delete or slim |
| N13 | Docs | workers-tooling fleet map, deployment, AGENTS/README process lists, regenerate CURRENT if scripted | “removed” + skill/CLI path |
| N14 | Tracker hygiene | comment/close #1713 | supersede |

## Slices

| # | Slice | Demo | Affordances |
|---|-------|------|-------------|
| V1 | **Code plane off** — remove adapter, hub client, port, CLI, bootstrap wiring, satellite helpers, contracts (if sole speaker), unit tests that only exist for them | Hub starts; no socialmedia client; `factory socialmedia-adapter` missing; targeted tests pass | N1–N6, N10–N12 |
| V2 | **Fleet plane off** — ACL matrix + fixtures (identity + hub pub/sub grants), secrets-policy, Quadlet component/unit/network decision, regen-authconf notes | Matrix has no **active** socialmedia-adapter; hub grants stripped; component section gone | N7–N9 |
| V3 | **Docs + trackers + ops checklist** — fleet map, deployment/README/AGENTS, CURRENT regen if applicable, #1713 supersede, host purge steps in PR | Fleet map row = removed + skill/CLI pointer; issue comment; ops checklist complete | N13–N14 |

**Landing rule:** land **V1+V2 in one PR** (do not merge code-off without matrix/grant strip). V3 may ride same PR. Split only if M₁ ACL regen needs a dedicated deploy window after merge.

### Ops rollout (M₁ — after merge)

Order (permission/identity drop):

1. Code/hub already off (no HB sub / no subject use) via V1.
2. Matrix + secrets-policy + Quadlet delete merged.
3. On M₁: `make nats-regen-authconf` (or project SSoT equivalent — **not** full seed `--regenerate` unless seeds compromised) → NATS restart + client restarts per `nats-authconf-update` runbook.
4. Host secret purge: `podman secret rm factory-nats-socialmedia factory-socialmedia-api-key` (if present); remove `nkeys/socialmedia-adapter.seed` + factory `socialmedia-api-key.tok` if present; re-run secrets-drift check. **Do not** touch `~/.roxabi/postiz` CLI token.
5. If unit ever existed: `systemctl --user disable --now factory-socialmedia-adapter` (or converge equivalent).
6. `postiz.network`: keep if Postiz stack owns/attaches it; only drop factory dual-home references.

## Success Criteria

- [ ] No `factory-socialmedia-adapter` component/unit in deploy SSoT (section **removed**, not merely `disabled`)
- [ ] No hub bootstrap of socialmedia NATS client; `SocialMediaClientProtocol` / `set_socialmedia_client` gone; `factory socialmedia-adapter` CLI entry removed
- [ ] `deploy/nats/acl-matrix.json` has no **active** `socialmedia-adapter` identity, no `factory.tool.socialmedia.>` request_reply flow, and **hub publish/subscribe grants** for those subjects stripped; ACL fixtures updated
- [ ] Repo greps: zero remaining `factory.tool.socialmedia` / `NatsSocialMediaClient` / `init_nats_socialmedia` in `src/` and `deploy/` (except changelog/history if any)
- [ ] Secrets-policy no longer requires `factory-nats-socialmedia` / factory-only socialmedia API key; PR documents host `podman secret rm` + seed file purge (ops checklist)
- [ ] `roxabi_contracts.socialmedia` + `roxabi_satellite.socialmedia` **removed** if monorepo-only speakers; if any external published consumer found, explicit deprecate-not-delete note in PR (no silent keep)
- [ ] `docs/architecture/workers-tooling.md` fleet map marks socialmedia-adapter **removed** with rationale; agent path documented as Postiz skill/CLI (pointer only — **no claim that a smoke was run unless recorded**)
- [ ] Related tests deleted or updated; qg green **and** absence greps above pass
- [ ] #1713 closed or explicitly superseded by comment pointing at #2329

## Edge Cases

| Case | Handling |
|---|---|
| Something still publishes to `factory.tool.socialmedia.>` | Pre-delete monorepo grep; if found, stop and either migrate caller or abort |
| `roxabi_contracts.socialmedia` imported outside this repo | Grep known monorepo consumers; published package consumers → deprecate path in PR notes, not silent hard-delete |
| Postiz skill/CLI broken | Does **not** block NATS plane removal; document path as pointer only unless smoke is explicitly run and recorded |
| Operator had manually enabled disabled component | Removal of unit + secrets makes enable impossible; note in PR/ops checklist |
| `auth.conf` on host stale after matrix change | Ops checklist: regen-authconf + NATS restart after merge; never hand-edit `auth.conf` |
| Lifecycle “retired” identity rows | Prefer no active identity + grants stripped; if lifecycle requires a retired row, that is OK **iff** no grants and regen applied — AC targets **active** plane |
| `postiz.network` still needed by non-factory units | Keep network file if Postiz stack references it; only drop factory dual-home |

## Out of Scope

- Shutting down Postiz
- In-hub HTTPS client + CB
- MCP socialmedia
- Scrape admission work
