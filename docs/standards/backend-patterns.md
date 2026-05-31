---
title: Backend Patterns — Lyra
description: Mandatory patterns for hub, adapters, LLM drivers, stores, and plugin commands — enforced at code review.
---

# Backend Patterns — Lyra

> Status: LIVING
> Scope: `src/lyra/` — core, adapters, llm, commands, infrastructure
> Source: `docs/architecture/architecture-patterns.md`, per-subpackage CLAUDE.md files

---

## Layer Boundaries

Lyra uses hexagonal (ports-and-adapters) architecture with four concentric layers:

```
Domain  →  Application  →  Infrastructure  →  Adapters
(core/)    (bootstrap/)    (infrastructure/)  (adapters/)
```

**Rule:** dependencies point inward only. No inner layer may import an outer layer.

| From \ To | Domain | Application | Infrastructure | Adapters |
|-----------|--------|-------------|----------------|----------|
| Domain | — | NEVER | NEVER | NEVER |
| Application | OK | — | NEVER | NEVER |
| Infrastructure | OK | OK | — | NEVER |
| Adapters | OK | OK | OK | — (lateral forbidden) |

Import-linter enforces these rules on every push (see `.importlinter`). Violations are merge blockers.

---

## Hub / Core (`src/lyra/core/`)

### Hub contract

`Hub` is the singleton coordinator. It owns `PoolManager`, `LocalBus`, `OutboundDispatcher` instances, and the agent registry. Do NOT add business logic to `Hub` — logic belongs in Middleware, Pool, or Agent.

### Pool isolation

`Pool` is one-per-conversation-scope and serialises turns. It never knows which platform the message came from. Never make `Pool` depend on `Hub` directly — use `PoolContext` (the narrow interface `Pool` requires from its owner).

### RoutingKey

`RoutingKey` is a `NamedTuple(platform, bot_id, scope_id)`. Always call `.to_pool_id()` to build pool IDs — never construct pool ID strings manually (ADR-001 §4).

```python
# Correct
key = RoutingKey(platform="telegram", bot_id="main", scope_id=12345)
pool_id = key.to_pool_id()

# Wrong
pool_id = f"telegram:main:{scope_id}"  # NEVER
```

### Middleware pipeline

The inbound pipeline is a composable middleware stack (`hub/middleware.py`). ErrorBoundaryMiddleware sits at position 0 — it catches LyraUserError and unhandled exceptions, dispatches a reply, and returns `_DROP`. Never silence exceptions above this boundary.

### Store pattern

All stores follow the async store pattern:

```python
# __init__: data structures only, no I/O
# connect(): open DB, run migrations, warm cache
# close(): teardown

store = AgentStore(path=db_path)
await store.connect()    # must be called before any read/write
value = store.get(key)   # synchronous read from cache
await store.set(key, v)  # async write to SQLite + cache update
await store.close()
```

Reads are synchronous (served from cache). Writes are async (SQLite). Never call async store methods before `connect()` or from synchronous code.

Store protocols live in `core/stores/`; concrete implementations live in `lyra.infrastructure.stores`.

---

## Adapters (`src/lyra/adapters/`)

### ChannelAdapter protocol

Every platform adapter must implement `ChannelAdapter` (defined in `core/hub/hub_protocol.py`):

| Method | Role |
|--------|------|
| `normalize(raw)` | Parse raw platform payload → `InboundMessage` |
| `normalize_audio(raw, bytes, mime, trust_level)` | Parse audio → AudioPayload |
| `send(original_msg, outbound)` | Send a complete reply |
| `send_streaming(original_msg, chunks, outbound)` | Stream reply with edit-in-place |
| `render_audio(msg, inbound)` | Send a voice note |
| `render_audio_stream(chunks, inbound)` | Stream TTS audio chunks |
| `render_attachment(msg, inbound)` | Send an attachment |

Do NOT override `send_streaming()` in a concrete adapter — it is a concrete method on `OutboundAdapterBase` that delegates to `StreamingSession`. Platform differences belong in `_make_streaming_callbacks()`.

### Inbound push

Always use `push_to_hub_guarded()` instead of calling `hub.push()` directly. It handles circuit breaker open state and backpressure.

```python
# Correct
await push_to_hub_guarded(hub, message, adapter)

# Wrong
await hub.push(message)  # NEVER — bypasses backpressure
```

### Authentication

Adapters must verify sender identity at the platform level before constructing an `InboundMessage`. The hub trusts `user_id` and `scope_id` from the message object.

- Telegram: validate `X-Telegram-Bot-Api-Secret-Token` header via HMAC.
- Discord: discord.py validates the connection; `message.author` is authenticated.

Never derive `user_id` or `scope_id` from unverified fields in the raw payload.

### File naming

Submodules are named `{platform}_{concern}.py`. The facade (`telegram.py`, `discord/adapter.py`) only imports from submodules — no logic in the facade. Formatting logic belongs in `{platform}_formatting.py` — not in outbound or inbound files.

### Discord typing indicator

Do NOT use `async with channel.typing()` — the context manager auto-refreshes every 5 s and triggers 429s under load. Instead call `await channel.typing()` manually every 9 s (see `_discord_typing_worker`).

---

## LLM Drivers (`src/lyra/llm/`)

### LlmProvider protocol

```python
class LlmProvider(Protocol):
    capabilities: dict[str, Any]
    async def complete(pool_id, text, model_cfg, system_prompt, *, messages, on_intermediate) -> LlmResult: ...
    def is_alive(pool_id) -> bool: ...
    async def stream(pool_id, text, model_cfg, system_prompt, *, messages) -> AsyncIterator[LlmEvent]: ...
```

`stream()` is duck-typed optional — callers check `hasattr(provider, "stream")`. Always check `result.ok` before using `result.result` from `LlmResult`.

### Driver selection

| Driver | When to use |
|--------|-------------|
| `ClaudeCliDriver` | Single-process mode (hub owns CliPool directly) |
| CliNatsDriver | Multi-process mode (hub sends to clipool worker over NATS) |
| NatsLlmDriver | Generic remote LLM worker (not claude-cli specific) |

### Decorator stack

```
CircuitBreakerDecorator → SmartRoutingDecorator → RetryDecorator → Driver
```

The stack is assembled in `bootstrap/` — never in `llm/`. Do NOT construct the decorator stack inside `llm/`.

### LlmEvent types

Events live in `lyra.core.messaging.events` (not in `llm/`) to preserve the unidirectional `llm → core` dependency:

| Event | Purpose |
|-------|---------|
| `TextLlmEvent(text)` | A chunk of streamed text |
| `ToolUseLlmEvent(tool_name, tool_id, input)` | LLM called a tool |
| `ResultLlmEvent(is_error, duration_ms, cost_usd)` | Turn complete (always last) |

All event classes are `frozen=True` — never mutate after construction. Import canonically from `lyra.core.messaging.events`.

### Retryability

`retryable=False` on `LlmResult` means the caller must NOT retry (e.g. quota exhausted, bad key). Default is `True` (transient failures are retriable). `ProviderAuthError` is always `retryable=False`.

---

## Commands / Plugins (`src/lyra/commands/`)

### Plugin structure

Each plugin is a subdirectory with a `plugin.toml` manifest and a `handlers.py` module.

```toml
name = "mycommand"
version = "0.1.0"
priority = 100          # lower = higher priority

[[commands]]
name = "mycommand"
description = "Description of what it does"
handler = "cmd_mycommand"
```

### Handler signatures

```python
# Plugin command handler (via plugin.toml)
async def cmd_example(msg: InboundMessage, pool: Pool, args: list[str]) -> Response: ...

# Session command handler (via agent.register_session_command())
async def cmd_example(
    msg: InboundMessage, driver: LlmProvider, tools: SessionTools,
    args: list[str], timeout: float,
) -> Response: ...
```

Always return `Response(content=...)` — never return `None` or raise from a handler.

### Command routing order

1. Built-in commands (`/help`, `/stop`, `/circuit`, `/config`, `/clear`, `/new`, `/folder`, `/workspace`) — always available
2. Session commands — registered by agents via `register_session_command()`
3. Plugin commands — discovered from `commands/` subdirectories

Built-in commands always win. Plugin commands cannot override built-ins.

### Forbidden command names

Do NOT create plugin names that conflict with built-ins: `help`, `stop`, `circuit`, `config`, `clear`, `new`, `workspace`, `folder`.

---

## Error Handling

### Error hierarchy

```
Exception
├── LyraUserError               # base for all user-visible errors (lyra.core.errors)
│   ├── AudioDownloadError
│   ├── AudioTooLargeError
│   ├── AudioInvalidFormatError
│   └── SttError
├── ProviderError               # LLM driver errors (src/lyra/errors.py)
│   ├── ProviderAuthError       # retryable=False
│   ├── ProviderRateLimitError  # retryable=True
│   └── ProviderApiError        # retryable=False by default
├── MissingCredentialsError
└── KeyringError
```

### Error boundary rule

LyraUserError subclasses are raised at the point of failure and caught by ErrorBoundaryMiddleware. They produce a user-visible reply via `MessageManager` template lookup (`key`) with a `fallback_text` for degraded mode.

Never raise LyraUserError from within a store or driver — raise a domain-specific subclass instead.

Never silently drop errors above ErrorBoundaryMiddleware — unhandled exceptions are caught there and translated into a generic error reply.

---

## Naming Conventions

| Artifact | Convention | Example |
|----------|------------|---------|
| Python files | `snake_case` | `telegram_inbound.py`, `hub_protocol.py` |
| Classes | `PascalCase` | `TelegramAdapter`, `PoolManager` |
| Functions / methods | `snake_case` | `push_to_hub_guarded`, `normalize_audio` |
| Protocol classes | `PascalCase` + `Protocol` suffix is optional | `LlmProvider`, `ChannelAdapter` |
| Platform submodules | `{platform}_{concern}.py` | `discord_formatting.py` |
| Store protocols | in `core/stores/`; ends with `_protocol.py` | `agent_store_protocol.py` |
| Infrastructure implementations | in `lyra.infrastructure.stores` | `agent_store.py` |
| Test files | `test_{module_under_test}.py` | `test_config.py` |
| Commands (slash) | lowercase alphanumeric + hyphens | `/search`, `/add-vault` |

---

## AI Quick Reference

ALWAYS use `RoutingKey.to_pool_id()` — never construct pool ID strings manually.

ALWAYS use `push_to_hub_guarded()` — never call `hub.push()` directly.

ALWAYS check `result.ok` before accessing `result.result` from `LlmResult`.

ALWAYS verify platform-level auth in adapters before constructing `InboundMessage`.

ALWAYS raise LyraUserError subclasses for user-visible failures; let ErrorBoundaryMiddleware catch them.

NEVER import an outer layer from an inner layer — dependencies point inward.

NEVER add business logic to `Hub` — it belongs in Middleware, Pool, or Agent.

NEVER call store async methods before `connect()` or from synchronous code.

NEVER override `send_streaming()` in a concrete adapter — put differences in `_make_streaming_callbacks()`.

NEVER put platform-specific code in `core/` — that belongs in `adapters/`.

NEVER construct the LLM decorator stack outside `bootstrap/`.

PREFER Protocol-based ports defined in `core/` over concrete types in outer layers.

PREFER frozen dataclasses for domain event types — mutate never, replace always.
