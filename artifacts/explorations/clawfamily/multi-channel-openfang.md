# OpenFang — Multi-Channel Architecture Analysis

> Source: https://github.com/RightNow-AI/openfang
> Analyzed: 2026-03-09
> Relevance: MEDIUM — Storage/search patterns, autonomous scheduling, orthogonal focus

## What it is

Agent Operating System (Rust, 32MB single binary) for autonomous agents that run on schedules without user prompts. 40 channel adapters, 7 pre-built "Hands" (autonomous capability packages), WASM sandbox for tool isolation.

## Channel Support

40 adapters across 5 waves:
- **Wave 1-2** (core): Telegram, Discord, Slack, WhatsApp, Signal, Matrix, Email, Teams, Google Chat, IRC, Mattermost
- **Wave 3** (enterprise): Salesforce, HubSpot, Jira, Confluence, Zendesk, Linear
- **Wave 4-5** (niche): Nostr, Bluesky, Twitter, LinkedIn, Reddit, Twitch, Mastodon, and more

## Event Loop Architecture (Rust Kernel)

Two-crate design:
- **`openfang-kernel`** — orchestration, workflows, metering, RBAC, scheduler, EventBus
- **`openfang-runtime`** — agent loop, 3 LLM drivers (27 providers), 53 tools, WASM sandbox

EventBus routes messages between agents, channels, external systems. Kernel evaluates message complexity to select appropriate LLM provider.

## Message Normalization

All 40 adapters convert to unified `ChannelMessage`. Platform-specific metadata preserved (no fidelity loss). Rate limiters and parsing per adapter.

## Routing

Kernel-level with RBAC validation. Capabilities immutable after agent creation. Supervisor + WorkflowEngine coordinate multi-step agents and inter-agent pairing.

## Per-User State

SQLite + vector embeddings for context retrieval. Per-agent sessions with isolated state and capability gates. Canonical sessions with 7-phase validation and auto-repair.

## Backpressure

Resource budget per agent. Scheduler manages queue of pending agents. WASM dual-metered sandbox:
- **Fuel metering** — counts WASM instructions, prevents CPU-intensive loops
- **Epoch metering** — enforces timeouts, prevents host call blocking

## Multi-Channel Fan-Out

"Hands" architecture: autonomous agents (Clip, Lead, Collector, Predictor, Researcher, Twitter, Browser) publish to multiple channels on schedule. No user prompt required.

## Memory

SQLite + vector embeddings, compaction optimization, session healing with auto-repair.

## Security (16 layers)

WASM dual-metered sandbox, Merkle hash-chain audit trails, cryptographic manifest signing, SSRF protection, secret zeroization, taint tracking, subprocess isolation, prompt injection scanning, path traversal prevention, GCRA rate limiting per IP.

## Key Insights for Lyra

1. **SQLite + vectors + FTS5** — same storage stack as roxabi-memory. Session healing (7-phase validation + auto-repair) is a reference for #83 session lifecycle.
2. **Autonomous scheduling** — Hands run without user prompts. Orthogonal to Lyra's current chat-reactive model, but relevant for Phase 2+ (scheduled summaries, proactive memory compaction).
3. **WASM sandbox for tools** — relevant if Lyra ever runs untrusted plugins. Reference for #106 future phases.
4. **Rate limiting per adapter** — GCRA per-IP rate limiting per channel. Missing from Lyra currently.
5. **Resource budget per agent** — prevent one user from consuming all LLM budget. Relevant when multi-user load increases.
