# Nanobot — Multi-Channel Architecture Analysis

> Source: https://github.com/HKUDS/nanobot
> Analyzed: 2026-03-09
> Relevance: HIGH — Python-first paradigm match, closest to Lyra's stack

## What it is

Ultra-lightweight personal AI assistant (~4K lines of core code). Research-friendly, Python-first, minimal and customizable. Supports 9+ messaging platforms via WebSocket persistent connections + IMAP polling fallback.

## Channel Support

| Platform | Protocol | Library |
|----------|----------|---------|
| Telegram | WebSocket | aiogram |
| Discord | WebSocket | discord.py |
| Matrix | WebSocket | matrix-nio |
| Slack | WebSocket Socket Mode | slack-sdk |
| DingTalk | Stream mode | dingtalk-stream |
| Feishu/Lark | WebSocket | lark-oapi |
| QQ | WebSocket | qq-botpy |
| Email | IMAP polling | — |
| WhatsApp | Bridge mode polling | — |

## Event Loop Architecture

Pure asyncio with `asyncio.gather(*tasks, return_exceptions=True)` to orchestrate all channels and services concurrently. Each channel runs as an independent `asyncio.Task` — failure in one channel doesn't crash others (exceptions are wrapped). Background tasks (cron, heartbeat) run concurrently without blocking message processing.

## Message Normalization — Two-Queue MessageBus

```
Channels → asyncio.Queue[InboundMessage] → Dispatcher → Agent
Agent    → asyncio.Queue[OutboundMessage] → Dispatcher → Channels
```

- **InboundMessage**: platform-agnostic fields (sender, text, metadata, session context)
- **OutboundMessage**: routes back to originating channel via JID/session mapping
- Channels normalize platform-specific events into `InboundMessage` on the way in

## Routing

Pub/sub MessageBus. Background `ChannelManager` task continuously consumes outbound messages and routes them to the correct channel based on JID/session key. Each session gets a dedicated `asyncio.Task` for non-blocking concurrent processing. `/stop` cancels per-session task.

## Per-User State

- Per-session `asyncio.Task` objects (concurrent across users)
- Session keys track per-user conversation state and tool execution context
- Shared memory store across all channels; channel-specific session manager per platform

## Backpressure

Queue-based implicit backpressure — if agent processing falls behind, inbound queue grows (bounded by memory). No explicit drop policy. Messages remain queued; slow LLM accumulates in memory rather than being dropped.

## Multi-Channel Fan-Out

Limited. Messages typically route to origin channel. Tool outputs can publish to MessageBus for dispatcher to route to subscribed channels if explicitly configured.

## Memory

Local conversation context in `~/.nanobot/`, channel-specific data segregated. Pluggable backend.

## Key Insights for Lyra

1. **Two-queue pattern** — split inbound/outbound is cleaner than Lyra's single queue → maps to #112
2. **Per-session Task** — better than per-user Lock (cancellable, timeouteable) → maps to #112
3. **`asyncio.gather` with `return_exceptions=True`** — prevents one adapter crash from killing the hub
4. **Channel abstraction** — each channel is a Task, making it trivial to add/remove channels at runtime
