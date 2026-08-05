---
title: "Plan: chore(socialmedia) decommission NATS adapter; keep Postiz + skill/CLI"
issue: 2329
spec: artifacts/specs/2329-decommission-nats-adapter-keep-postiz-skill-cli-spec.md
complexity: 4/10
tier: F-lite
generated: 2026-08-05
---

## Summary

Delete the unused socialmedia NATS tool plane (adapter, hub client, ACL, Quadlet, contracts/satellite helpers) in **one PR** (V1+V2+V3). Keep Postiz + agent skill/CLI. Land code-off and fleet grant strip together; document host regen-authconf + secret purge in PR body.

## Architecture

**Data flow:** [before/after](../visuals/2329-decommission-nats-adapter-keep-postiz-skill-cli-data-flow.html)
**File map:** [remove/edit](../visuals/2329-decommission-nats-adapter-keep-postiz-skill-cli-file-map.html)

## Agents

| Agent instance | Tasks | Subjects | Files |
|---|---|---|---|
| backend-dev-A | T2–T4 | delete-src, bootstrap, cli | `src/factory/**` socialmedia plane |
| backend-dev-B | T5–T6 | packages, contracts | `packages/roxabi-{contracts,satellite}/**` |
| devops-A | T7–T8 | acl, deploy | `deploy/**`, ACL fixtures |
| tester-A | T9 | tests | `tests/**` |
| doc-writer-A | T10 | docs | `docs/**`, AGENTS, README |
| backend-dev-A (gate) | T1, T11 | grep, qg | repo-wide verify |

## Wave Structure

3 waves, max 3 parallel agents.

| Wave | Trigger | Agents | Tasks |
|------|---------|--------|-------|
| 1 | start | 1 | backend-dev-A: T1 (pre-grep) |
| 2 | Wave 1 clean | 3 ∥ | backend-dev-A: T2→T4 · backend-dev-B: T5→T6 · devops-A: T7→T8 |
| 3 | Wave 2 done | 2 ∥ then gate | tester-A: T9 · doc-writer-A: T10 · then backend-dev-A: T11 RED-GATE |

### Budget — per task

| Task | Items | Class | Est. ops | Split? |
|------|-------|-------|----------|--------|
| T1 pre-grep | 1 | bounded | 3 | — |
| T2 delete adapter+nats client | ~15 files | bounded | 4 | — |
| T3 unwire bootstrap+port | ~8 files | judgmental | 6 | — |
| T4 remove CLI + fleet_catalog + importlinter | 3 | trivial | 2 | — |
| T5 satellite package | 5 | bounded | 3 | — |
| T6 contracts package | 5 | bounded | 3 | — |
| T7 ACL matrix + fixtures | 4 | judgmental | 6 | — |
| T8 secrets + quadlet | 5 | bounded | 4 | — |
| T9 tests | 6 | judgmental | 6 | — |
| T10 docs | 5 | bounded | 4 | — |
| T11 RED-GATE | 1 | bounded | 4 | — |

**Total estimated ops: ~45**

### Budget — per agent instance

| Instance | Tasks | Σ ops | Subjects | Split? |
|----------|-------|-------|----------|--------|
| backend-dev-A | T1,T2,T3,T4,T11 | 19 | delete-src, bootstrap, gate | — |
| backend-dev-B | T5,T6 | 6 | packages | — |
| devops-A | T7,T8 | 10 | acl, deploy | — |
| tester-A | T9 | 6 | tests | — |
| doc-writer-A | T10 | 4 | docs | — |

## Consistency Report

- Spec criteria: 9 · covered by tasks: 9 · uncovered: 0
- Breadboard N1–N14 → tasks traced
- Untraced tasks: 0
- Exemptions: live M₁ `make nats-regen-authconf` is **ops follow-up** documented in PR, not automated in CI

## Micro-Tasks

### Slice V1+V2+V3 (single PR)

#### T1 — Pre-delete consumer grep
- **Agent:** backend-dev-A · **Subject:** gate · **Phase:** RED · **Difficulty:** 1
- **Spec trace:** SC absence greps · Edge pre-delete
- **Files:** repo-wide
- **Description:** Grep monorepo for `factory.tool.socialmedia`, `NatsSocialMediaClient`, `init_nats_socialmedia`, `roxabi_contracts.socialmedia`, `factory.adapters.socialmedia`. Confirm no unexpected external speakers. Abort delete of contracts if external published consumer found (document deprecate instead).
- **Verify:** `rg -n 'factory\.tool\.socialmedia|NatsSocialMediaClient|init_nats_socialmedia' src deploy packages tests` shows only known paths
- **Expected:** inventory matches spec; proceed

#### T2 — Delete adapter + hub NATS client packages
- **Agent:** backend-dev-A · **Subject:** delete-src · **Phase:** GREEN · **[P]** after T1 · **Difficulty:** 2
- **Spec trace:** N1, N2 · SC hub/CLI
- **Files:** `src/factory/adapters/socialmedia/**`, `src/factory/nats/socialmedia/**`
- **Description:** Remove both packages entirely.
- **Verify:** paths absent
- **Expected:** no directory remains

#### T3 — Unwire hub port + bootstrap
- **Agent:** backend-dev-A · **Subject:** bootstrap · **Phase:** GREEN · deps T2 · **Difficulty:** 3
- **Spec trace:** N3, N4
- **Files:** `src/factory/core/ports/socialmedia.py`, `src/factory/core/hub/hub.py`, `hub_registration.py`, `bootstrap/factory/voice_overlay.py`, `wiring_helpers.py`, `hub/hub_assembly.py`, `hub/hub_core.py`, `unified.py`, `bootstrap/types.py`
- **Description:** Remove `SocialMediaClientProtocol`, `set_socialmedia_client`, `_socialmedia_client`, `init_nats_socialmedia`, start/stop wiring, type fields.
- **Verify:** `rg -n socialmedia src/factory/core src/factory/bootstrap` empty or only unrelated
- **Expected:** hub boots without socialmedia client

#### T4 — CLI + fleet_catalog + importlinter
- **Agent:** backend-dev-A · **Subject:** cli · **Phase:** GREEN · deps T2 · **Difficulty:** 1
- **Spec trace:** N5, N6
- **Files:** `src/factory/cli/main.py`, `src/factory/nats/fleet_catalog.py`, `.importlinter`
- **Description:** Remove `socialmedia-adapter` command; drop catalog entry; drop `factory.adapters.socialmedia` from importlinter if listed.
- **Verify:** `factory socialmedia-adapter --help` fails / command gone; catalog has no socialmedia-adapter
- **Expected:** clean

#### T5 — Remove roxabi_satellite.socialmedia
- **Agent:** backend-dev-B · **Subject:** packages · **Phase:** GREEN · **[P]** after T1 · **Difficulty:** 2
- **Spec trace:** N10
- **Files:** `packages/roxabi-satellite/src/roxabi_satellite/socialmedia/**`, `packages/roxabi-satellite/tests/test_socialmedia.py`, README package refs
- **Description:** Delete module + tests; update package README/pyproject description if it lists socialmedia.
- **Verify:** no `roxabi_satellite.socialmedia` imports
- **Expected:** package tests still collect without socialmedia module

#### T6 — Remove roxabi_contracts.socialmedia (if sole speaker)
- **Agent:** backend-dev-B · **Subject:** packages · **Phase:** GREEN · deps T1 · **Difficulty:** 2
- **Spec trace:** N11
- **Files:** `packages/roxabi-contracts/src/roxabi_contracts/socialmedia/**`, related tests, package exports
- **Description:** If T1 found only factory/satellite speakers, delete contracts package subtree + tests. Leave `roxabi_contracts.postiz` if unrelated. Update package `__init__`/exports if any.
- **Verify:** contracts tests pass without socialmedia
- **Expected:** deleted or PR note if kept

#### T7 — ACL matrix + fixtures + hub grants
- **Agent:** devops-A · **Subject:** acl · **Phase:** GREEN · **[P]** after T1 · **Difficulty:** 3
- **Spec trace:** N7 · SC matrix + hub grants
- **Files:** `deploy/nats/acl-matrix.json`, `tests/scripts/fixtures/v3-current.json`, `tests/scripts/fixtures/v3-pre-grant-group.json` (and any other fixtures with socialmedia)
- **Description:** Remove socialmedia-adapter identity block; remove request_reply_flows entry; strip hub publish `factory.tool.socialmedia.>` and subscribe `factory.tool.socialmedia.heartbeat`. Do **not** hand-edit `auth.conf` (document regen).
- **Verify:** `rg socialmedia deploy/nats/acl-matrix.json tests/scripts/fixtures` empty
- **Expected:** matrix clean

#### T8 — Secrets policy + Quadlet
- **Agent:** devops-A · **Subject:** deploy · **Phase:** GREEN · deps T7 optional · **Difficulty:** 2
- **Spec trace:** N8, N9 · SC component removed
- **Files:** `deploy/secrets-policy.toml`, `deploy/quadlet.toml`, `deploy/quadlet/factory-socialmedia-adapter.container`, `deploy/quadlet/postiz.network`
- **Description:** Remove secret entries for factory-nats-socialmedia and factory-socialmedia-api-key if adapter-only. Delete `[component.socialmedia-adapter]` section (not leave disabled). Delete `.container` unit. Keep `postiz.network` only if still required by non-factory docs; else delete or leave with comment ownership = Postiz stack.
- **Verify:** no component.socialmedia-adapter; unit file gone
- **Expected:** deploy SSoT clean

#### T9 — Tests cleanup
- **Agent:** tester-A · **Subject:** tests · **Phase:** GREEN · deps T2–T6 · **Difficulty:** 3
- **Spec trace:** SC qg
- **Files:** `tests/nats/test_nats_socialmedia_client.py`, `tests/adapters/test_socialmedia_slug.py`, `tests/bootstrap/test_wiring_helpers.py`, `tests/bootstrap/test_voice_overlay.py`, `tests/bootstrap/test_unified.py`, other socialmedia refs
- **Description:** Delete obsolete tests; update bootstrap tests that mock `init_nats_socialmedia` / assert socialmedia_client.
- **Verify:** `pytest` on touched tests green
- **Expected:** no import errors

#### T10 — Docs + trackers
- **Agent:** doc-writer-A · **Subject:** docs · **Phase:** GREEN · deps T8 · **Difficulty:** 2
- **Spec trace:** N13, N14 · SC fleet map
- **Files:** `docs/architecture/workers-tooling.md`, `docs/architecture/deployment.md`, `AGENTS.md`, `README.md`, regenerate `CURRENT.generated.md` if scripted, issue #1713
- **Description:** Fleet map row → **removed** + rationale; agent path = Postiz skill/CLI (pointer, no vacuous smoke claim). Fix process lists that name socialmedia-adapter. Comment/close #1713 as superseded by #2329.
- **Verify:** docs no longer list adapter as live capability
- **Expected:** narrative matches decommission

#### T11 — RED-GATE absence + qg
- **Agent:** backend-dev-A · **Subject:** gate · **Phase:** RED-GATE · deps T2–T10 · **Difficulty:** 2
- **Spec trace:** all SC
- **Files:** —
- **Description:** Run absence greps on `src/` + `deploy/`; run project qg / targeted pytest; PR body includes ops checklist (regen-authconf, NATS restart, podman secret rm, seed purge, systemctl disable if ever enabled).
- **Verify:**
  ```bash
  ! rg -n 'factory\.tool\.socialmedia|NatsSocialMediaClient|init_nats_socialmedia|factory-socialmedia-adapter' src deploy
  # qg or: uv run pytest tests/nats tests/bootstrap tests/adapters -q --tb=no
  ```
- **Expected:** greps clean; qg green

## Task Seeding Blueprint

### Wave 1 — no deps, 1 agent

| Task | Agent instance | blockedBy | Subject |
|------|---------------|-----------|---------|
| T1 | backend-dev-A | — | gate |

### Wave 2 — after Wave 1, 3 agents ∥

| Task | Agent instance | blockedBy | Subject |
|------|---------------|-----------|---------|
| T2 | backend-dev-A | T1 | delete-src |
| T3 | backend-dev-A | T2 | bootstrap |
| T4 | backend-dev-A | T2 | cli |
| T5 | backend-dev-B | T1 | packages |
| T6 | backend-dev-B | T1 | packages |
| T7 | devops-A | T1 | acl |
| T8 | devops-A | T7 | deploy |

### Wave 3 — after Wave 2

| Task | Agent instance | blockedBy | Subject |
|------|---------------|-----------|---------|
| T9 | tester-A | T2,T3,T4,T5,T6 | tests |
| T10 | doc-writer-A | T8 | docs |
| T11 | backend-dev-A | T9,T10 | gate |

## Task IDs

<!-- Generated by /plan. Used by /implement to resume tasks on session restart. -->
- T1: t1-pre-grep — gate
- T2: t2-delete-src — delete-src
- T3: t3-bootstrap — bootstrap
- T4: t4-cli-catalog — cli
- T5: t5-satellite — packages
- T6: t6-contracts — packages
- T7: t7-acl — acl
- T8: t8-deploy — deploy
- T9: t9-tests — tests
- T10: t10-docs — docs
- T11: t11-red-gate — gate
