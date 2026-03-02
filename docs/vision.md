# Vision — Lyra by Roxabi

## What is Lyra?

Lyra is a personal AI agent engine. It runs 24/7 on your own hardware, connects to any messaging channel (Telegram, Discord, Signal…), and routes conversations to specialized agents that use your own LLMs, memory, and tools.

No subscription. No cloud lock-in. Your data stays on your machines.

## Why build it?

Every existing solution forces a trade-off:

| Solution | Problem |
|----------|---------|
| ChatGPT / Claude web | Your data leaves, no memory, no automation, no integrations |
| LangChain / LlamaIndex | Framework, not a product — you build everything yourself |
| Open-source bots | Single-channel, single-agent, no real memory |
| Self-hosted SaaS (n8n, Flowise) | Visual tools, hard to extend, no real agent reasoning |

Lyra takes a different approach: a minimal, auditable core (hub + bus + pools) that you own entirely, with a clean extension model for adapters, agents, and skills.

## Design principles

**1. Lightweight core, rich extensions.**
The hub is ~300 lines. Everything else (adapters, agents, skills, memory) plugs in cleanly without touching the core.

**2. Stateless agents, stateful pools.**
An agent is an immutable config — system prompt, permissions, memory namespace. All mutable state lives in the Pool. No race conditions, no hidden side effects.

**3. Sequential per user, parallel across users.**
Each pool has an `asyncio.Lock`. Two messages from the same user are processed in order. Two messages from different users are processed in parallel. Zero extra configuration.

**4. Local inference as a privacy selling point.**
Sensitive data (legal documents, medical records) never leaves the machine. Machine 2 runs heavy LLMs via an OpenAI-compatible API. Machine 1 handles TTS and embeddings.

**5. Auditable by design.**
No magic. Every routing decision, every memory write, every skill invocation is traceable. The architecture is readable in an afternoon.

## Hardware model

```
Machine 1 (Hub) — 24/7              Machine 2 (AI Server) — on demand
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━       ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
RTX 3080 10GB                        RTX 5070Ti 16GB
TTS + embeddings (~5.5GB VRAM)       Heavy LLM: Qwen 2.5 14B / Gemma 3 27B
Hub + channels + database            Exposed as OpenAI-compatible API
```

Cloud LLM (Anthropic) is the default in Phase 1. Local LLM on Machine 2 is added in Phase 2 as a cost control and privacy layer.

## What Lyra is not

- Not a general-purpose framework to sell as a product
- Not a replacement for task managers or calendars
- Not an autonomous agent that acts without supervision
- Not designed for multi-tenant or high-concurrency production use

It is a personal tool, built for one operator (you), running on hardware you control.

## Roadmap in one line

Phase 1 → hub + Telegram + cloud LLM + semantic memory.
Phase 2 → local LLM + atomic SLMs + cognitive meta-language.
Phase 3 → LegalTech SaaS if social signals are positive.
