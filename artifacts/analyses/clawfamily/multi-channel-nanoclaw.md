# NanoClaw — Multi-Channel Architecture Analysis

> Source: https://github.com/qwibitai/nanoclaw
> Analyzed: 2026-03-09
> Relevance: MEDIUM — Novel isolation patterns, simplicity-first philosophy

## What it is

Lightweight AI assistant framework with containerized agent isolation. TypeScript/Node.js single orchestrator. Emphasizes security and simplicity over feature breadth. "Small enough to understand" philosophy.

## Channel Support

5 primary channels: WhatsApp, Telegram, Discord, Slack, Gmail. Self-registering factory pattern enables pluggable adapters.

## Event Loop Architecture

Single Node.js process with three concurrent polling loops:
1. Message processing (per-group SQLite polling)
2. Task scheduling (cron jobs)
3. IPC monitoring (filesystem watches)

Channels self-register at startup. Dual cursors for crash recovery:
- `lastTimestamp` — global cursor
- `lastAgentTimestamp` — per-group cursor

On crash: cursor rolls back, message re-processed. On success: cursor advances (prevents duplicates).

## Message Normalization

Unified SQLite schema — all channels write to the same message table:
```
messages(group, sender, timestamp, content, processed_by_agent)
```
JID (Jabber ID equivalent) namespacing: `whatsapp:1234567890`, `telegram:chat_id`. `findChannel(jid)` locates the owning adapter.

## Routing — GroupQueue with Bounded Concurrency

```
SQLite messages → GroupQueue → Container spawn (max 3-5 concurrent)
```

Per-group FIFO queue limits concurrent containers. Work prioritization: pending tasks execute before message discovery (prevents polling from blocking long operations).

**GroupQueue state:**
- `activeCount` — running containers
- `waitingGroups` — queued when capacity exhausted
- `pendingTasks` — explicit job submissions
- `pendingMessages` — message processing flags

## Per-User State (Container Isolation)

Each message invocation spawns an ephemeral Docker/Apple Container:
- **Main groups**: project root (read-only) + writable group folder
- **Non-main groups**: group folder + read-only global memory + isolated `.claude/` directories
- Per-group `CLAUDE.md` files for memory isolation
- IPC via filesystem JSON files in per-group directories

## Backpressure

Explicit capacity rejection: `activeCount >= MAX` → add to `waitingGroups`. Exponential backoff on retry: `BASE_RETRY_MS * 2^(retryCount-1)`, max 5 retries. Idle timeout: containers receive typing indicator, stdin closed after inactivity.

## Multi-Channel Fan-Out

Container calls router to send messages to multiple channels. Responses flow through IPC file writes back to host.

## Key Insights for Lyra

1. **Dual-cursor crash recovery** — global + per-group cursor. Roll back on failure, advance on success. Clean idempotency for #67 (session persistence).
2. **Exponential backoff with max retries** — `BASE * 2^retry` pattern. Missing from Lyra's current error handling.
3. **Skills self-register at startup** — channels/skills call `register_channel(name, factory_fn)`. Direct model for #106 (plugin system MVP).
4. **Per-group CLAUDE.md** — memory isolation at the group level without a full memory system.
5. **Bounded concurrency** — explicit `MAX_CONCURRENT_CONTAINERS` with queue. Better than Lyra's implicit queue bound.
