---
title: "chore(socialmedia): decommission NATS adapter; keep Postiz + skill/CLI"
issue: 2329
status: approved
tier: F-lite
date: 2026-08-05
---

## Problem

Factory runs a NATS satellite (`factory-socialmedia-adapter`) that only bridges `factory.tool.socialmedia.>` to Postiz’s Public HTTP API. The hub wires `NatsSocialMediaClient` at bootstrap but has **no product caller** (no command/tool that publish/list/schedule). Agents already reach Postiz via skill + CLI (`~/.roxabi/postiz/bin/postiz`). Keeping a family-A satellite for a thin unused bridge violates capability admission (#2326 / workers-tooling Rule 2): self-hosted HTTP with a clean API is not a satellite by default.

## Who

- **Primary:** operators / fleet owners (fewer containers, nkeys, ACL rows, heartbeats to babysit)
- **Secondary:** agents/operators who publish via Postiz skill/CLI (must keep working; path unchanged)

## Constraints

- Postiz backing stays up; do not replace or shut it down
- Principal stays on `staging`; work on `feat/2329-decommission-nats-adapter-keep-postiz-skill-cli` worktree only
- Stage axis: decommission is infra/deploy + thin tool-plane removal — not a new platform adapter
- Confirm zero live NATS consumers before deleting ACL/identity
- qg/importlinter must stay green after deletions

## Out of Scope

- Replacing or decommissioning Postiz itself
- In-hub `HttpSocialMediaClient` + circuit-breaker (only if a future fleet process must publish without agents)
- MCP for socialmedia (optional later; skill/CLI is enough)
- Scrape or other capability admissions

## Premise Validity

**Success in 6 months:** No `factory-socialmedia-adapter` (or socialmedia NATS identity) in the prod process set; fleet map marks it removed; agents still publish via Postiz skill/CLI; ACL/nkey inventory has no orphan socialmedia satellite rows.

**Failure in 6 months:** Adapter still deployed “just in case,” or hub re-grows a dead NATS client / half-deleted path that still heartbeats or holds secrets; or agent publish path breaks without a documented replacement.

**Simplest alternative:** Leave the adapter running and document “isolation value.”
**Why not simplest:** Isolation value is unproven (unused hub client, HTTP already the real provider); cost is real (container, nkey, ACL, heartbeat noise, code surface).

## Complexity

**Tier: F-lite** — clear single-domain decommission (socialmedia tool plane + deploy), no new architecture; size label `size:F-lite`.

Signals:
- Issue label `size:F-lite`
- Delete/rewire existing paths; no new product feature
- Multi-file but one domain (adapter + client + deploy + docs)
