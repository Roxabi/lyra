# MoChat — Multi-Channel Architecture Analysis

> Source: https://github.com/HKUDS/MoChat
> Analyzed: 2026-03-09
> Relevance: LOW-MEDIUM — Agent federation patterns, not directly applicable now

## What it is

Agent-native social networking platform. Not a standalone framework — a coordination layer on top of OpenClaw, Nanobot, and Claude Code. Enables AI agents to discover collaborators and bridge communities across decentralized networks.

## What it is NOT

Not a messaging framework. MoChat provides a social layer *above* the messaging infrastructure. Agents register on MoChat to get identity, DMs, and group participation with other agents.

## Architecture

```
OpenClaw/Nanobot/Claude Code
         │
    MoChat Adapter
         │
    MoChat Platform (agent social network)
         │
    Agent Identity + DMs + Groups + Skill Discovery
```

Per-framework adapters handle framework-specific communication. Single REST API with `X-Claw-Token` auth.

## API Surface

| Endpoint | Purpose |
|----------|---------|
| `POST /api/claw/agents/selfRegister` | Register agent on platform |
| `POST /api/claw/agents/bind` | Bind to user accounts |
| `POST /api/claw/sessions/*` | Create/send/list sessions |

WebSocket (Socket.io) for real-time updates.

## Agent Registration Flow

1. Agent reads skill files
2. Calls `selfRegister()` — gets MoChat identity
3. Binds to email
4. Receives DMs automatically
5. Autonomously discovers collaborators based on skills/interests
6. Creates group introductions for complementary agents

## Key Insights for Lyra

1. **Agent-to-agent federation** — agents as first-class social entities with DMs and discovery. Relevant for Phase 5 (multi-agent orchestration, #63).
2. **Skill-based agent discovery** — agents advertise capabilities and find complementary agents. Model for future Lyra agent marketplace.
3. **Adapter pattern** — thin per-framework adapters means MoChat doesn't care about underlying transport. Clean separation.

## Current Relevance

Low for Phase 0-1b. Becomes relevant in Phase 5 (multi-agent) when Lyra agents need to coordinate with external agents or other Lyra instances.
