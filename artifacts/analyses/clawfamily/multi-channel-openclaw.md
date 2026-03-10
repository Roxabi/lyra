# OpenClaw — Multi-Channel Architecture Analysis

> Source: https://github.com/openclaw/openclaw
> Analyzed: 2026-03-09
> Relevance: HIGH — Architectural twin, direct reference for hub-and-spoke multi-channel

## What it is

Self-hosted personal AI assistant with a hub-and-spoke control plane coordinating 25+ messaging channels. TypeScript/Node.js. Production-grade, companion apps (macOS, iOS, Android).

## Channel Support

25+ channels including: Telegram, Discord, WhatsApp, Signal, iMessage (BlueBubbles), Slack, Teams, Google Chat, IRC, Matrix, LINE, Mattermost, Nextcloud Talk, Nostr, Twitch, Zalo, and more.

## Event Loop Architecture

**WebSocket server** as central Gateway (`ws://127.0.0.1:18789`). All channel adapters connect as clients to this local control plane. Event-driven: clients subscribe to events rather than polling. Single process per host (prevents WhatsApp multi-login conflicts).

```
Channel Adapters ──WebSocket──► Gateway (control plane)
                                    │
                              Session Router
                                    │
                              Agent Workspace
```

## Message Normalization — Six-Phase Journey

1. **Ingestion** — Channel adapter parses platform-specific event
2. **Access Control** — Allowlists and pairing policies gate message
3. **Context Assembly** — System prompt built from workspace files + session history + memory search
4. **Model Invocation** — Streaming LLM response generation
5. **Tool Execution** — Model tool calls intercepted and executed (Docker sandboxes optional)
6. **Response Delivery** — Formatted output sent through channel adapter back to user

## Routing — Session-Based

Routing key: `(channel, peer, context)` → `agent_id`. Session types:
- `agent:<id>:main` — full access
- `agent:<id>:<channel>:dm:<id>` — sandboxed DM
- `agent:<id>:<channel>:group:<id>` — sandboxed group

Multiple channels can map to the same agent but different sessions. Explicit binding system.

## Per-User State

**Append-only JSONL** per session at `~/.openclaw/agents/<agentId>/sessions/<SessionId>.jsonl`. Session registry `sessions.json` tracks metadata. Agent workspaces isolated: `SOUL.md`, `AGENTS.md`, `USER.md`, `TOOLS.md`.

## Backpressure

Streaming responses (no central queue). Per-session event loops prevent one slow session blocking others. Long-running tools block session processing until timeout.

## Multi-Channel Fan-Out

Via MCP tools — agent executes "send message to Slack" as a tool call, routing through channel adapter to different user/platform. Session branching creates alternate conversation paths.

## Memory

Pluggable memory backends (SQLite, Redis, vector DB). Bindings persist across restarts. Sub-workers inherit parent config (credentials, memory, permissions) — zero boilerplate for specialized agents.

## Key Insights for Lyra

1. **Persistent bindings across restarts** — Lyra's bindings are in-memory, lost on restart. #67 + #83 partially address this.
2. **Append-only JSONL per session** — enables crash recovery + session branching. Direct model for #67.
3. **Sub-worker config inheritance** — spawning a specialized agent that inherits parent context. Relevant for #99 (hub command sessions).
4. **Session types with isolation levels** — DM vs group vs main session with different trust boundaries.
5. **WebSocket gateway as control plane** — decouples channel adapters from hub logic. More robust than direct import.
