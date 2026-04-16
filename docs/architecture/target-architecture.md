# Architecture Manifesto — Hexagonal / Ports & Adapters

> **Status: STANDARD — ✅ Implemented**
> Date: 2026-03-19 (original) · 2026-04-16 (updated to reflect implementation truth)
> Scope: Reference standard for all Roxabi projects

---

## Principle

Architecture hexagonale (Cockburn — Ports & Adapters).
The **Domain Core** knows no framework, no channel, no LLM provider.
Everything specific (Telegram, Discord, Claude, Anthropic SDK) is an **Adapter** behind a **Port**.

### Invariants

1. Domain Core never imports `aiogram`, `discord`, `anthropic`, `httpx`
2. `StreamProcessor` is testable in isolation (no network)
3. A new outbound adapter only needs to understand `RenderEvent`
4. A new LLM adapter only needs to implement `LlmProvider` protocol

---

## Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        INBOUND ADAPTERS                         │
│         Telegram │ Discord │ Signal │ HTTP │ CLI                │
└──────────────────────────┬──────────────────────────────────────┘
                           │ normalize() → InboundMessage
┌──────────────────────────▼──────────────────────────────────────┐
│                      INBOUND PORT                               │
│              InboundMessage (frozen dataclass)                  │
│    { text, user_id, platform, trust_level, attachments, … }    │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                       DOMAIN CORE                               │
│                                                                 │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│  │  GuardChain │  │    Router    │  │   SessionManager      │  │
│  │  AuthGuard  │  │  commands →  │  │   compaction          │  │
│  │  RateLimit  │  │  plugins →   │  │   context             │  │
│  │  BlockGuard │  │  LLM         │  │   memory              │  │
│  └─────────────┘  └──────────────┘  └───────────────────────┘  │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                   StreamProcessor                        │   │
│  │   input:  AsyncIterator[LlmEvent]                        │   │
│  │   output: AsyncIterator[RenderEvent]                     │   │
│  │   logic: thresholds, grouping, throttle — channel-agnostic│   │
│  └──────────────────────────────────────────────────────────┘   │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                        LLM PORT                                 │
│   Protocol LlmProvider:                                         │
│     complete(…) → LlmResult          (batch, no streaming)     │
│     stream(…)   → AsyncIter[LlmEvent] (streaming, tool-aware)  │
│     is_alive(pool_id) → bool                                    │
└──────────────────────────┬──────────────────────────────────────┘
                           │ implement
┌──────────────────────────▼──────────────────────────────────────┐
│                       LLM ADAPTERS                              │
│      AnthropicSdkDriver │ ClaudeCliDriver │ NatsLlmDriver       │
└──────────────────────────┬──────────────────────────────────────┘
                           │ LlmEvent stream
┌──────────────────────────▼──────────────────────────────────────┐
│                    OUTBOUND PORT                                │
│   AsyncIterator[RenderEvent]                                    │
│   RenderEvent = TextRenderEvent | ToolSummaryRenderEvent        │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                   OUTBOUND ADAPTERS                             │
│   OutboundAdapterBase.send_streaming(events) → StreamingSession │
│   PlatformCallbacks inject platform-specific API calls           │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│                     OUTBOUND ADAPTERS                           │
│         Telegram │ Discord │ Signal │ HTTP │ CLI                │
│   implement: send(RenderEvent) — each in its own way            │
│   Telegram: editMessage in place                                 │
│   Discord:  update embed                                         │
│   CLI:      colored print                                        │
└─────────────────────────────────────────────────────────────────┘
```

---

## Canonical Types

### LlmEvent (emitted by LLM adapters)

Defined in `src/lyra/core/events.py`.

```python
@dataclass(frozen=True)
class TextLlmEvent:
    text: str

@dataclass(frozen=True)
class ToolUseLlmEvent:
    tool_name: str          # "Edit" | "Read" | "Bash" | "Grep" | …
    tool_id: str
    input: dict[str, Any]   # raw tool parameters

@dataclass(frozen=True)
class ResultLlmEvent:
    is_error: bool
    duration_ms: int
    cost_usd: float | None = None      # None for ClaudeCliDriver
    error_text: str | None = None      # backend-reported error message

LlmEvent = TextLlmEvent | ToolUseLlmEvent | ResultLlmEvent
```

### RenderEvent (emitted by StreamProcessor)

Defined in `src/lyra/core/render_events.py`.

```python
@dataclass(frozen=True)
class SilentCounts:
    reads: int = 0
    greps: int = 0
    globs: int = 0

@dataclass(frozen=True)
class FileEditSummary:
    path: str
    edits: list[str]        # function names (if ≤ threshold)
    count: int              # total edits on this file

@dataclass(frozen=True)
class TextRenderEvent:
    text: str
    is_final: bool          # True = last message of session
    schema_version: int = 1
    is_error: bool = False  # True when originating ResultLlmEvent.is_error

@dataclass(frozen=True)
class ToolSummaryRenderEvent:
    files: dict[str, FileEditSummary]
    bash_commands: list[str]
    web_fetches: list[str]       # URLs fetched
    agent_calls: list[str]       # agent descriptions
    silent_counts: SilentCounts  # reads, greps, globs
    is_complete: bool            # True = result received
    schema_version: int = 1

RenderEvent = TextRenderEvent | ToolSummaryRenderEvent
```

### LlmProvider Protocol

Defined in `src/lyra/llm/base.py`.

```python
@runtime_checkable
class LlmProvider(Protocol):
    capabilities: dict[str, Any]

    async def complete(
        self, pool_id: str, text: str, model_cfg: ModelConfig,
        system_prompt: str, *, messages: list[dict] | None = None,
    ) -> LlmResult: ...

    async def stream(
        self, pool_id: str, text: str, model_cfg: ModelConfig,
        system_prompt: str, *, messages: list[dict] | None = None,
    ) -> AsyncIterator[LlmEvent]: ...

    def is_alive(self, pool_id: str) -> bool: ...


@dataclass
class LlmResult:
    result: str = ""
    session_id: str = ""
    error: str = ""
    retryable: bool = True      # False = don't retry (auth, quota)
    warning: str = ""
    user_message: str = ""

    @property
    def ok(self) -> bool:
        return not self.error
```

---

## StreamProcessor — Logic

Implemented in `src/lyra/core/stream_processor.py`.

```
For each LlmEvent received:

  TextLlmEvent
    → accumulate text in _pending_text
    → if show_intermediate and text precedes tool call:
         yield TextRenderEvent(text, is_final=False)
         clear _pending_text

  ToolUseLlmEvent
    → accumulate into tool buckets:
       Edit/Write → files[path].edits (names mode) or count mode
       Bash       → bash_commands (truncated to bash_max_len)
       Read/Grep/Glob → silent_counts++
       WebFetch/WebSearch → web_fetches (if show.web_fetch)
       Agent → agent_calls (if show.agent)
    → if throttle elapsed: yield ToolSummaryRenderEvent(snapshot)

  ResultLlmEvent
    → if any tool events: yield ToolSummaryRenderEvent(is_complete=True)
    → yield TextRenderEvent(final_text, is_final=True, is_error=Result.is_error)
```

### Config (in `config.toml`)

```toml
[tool_display]
names_threshold = 3     # edits before switch to count mode per file
group_threshold = 3     # files before grouping
bash_max_len    = 60    # max chars for bash command display
throttle_ms     = 2000  # min delay between outbound updates

[tool_display.show]
read       = false
glob       = false
grep       = false
web_fetch  = true
web_search = true
agent      = true
bash       = true
write      = true
edit       = true
```

---

## StreamingSession — Outbound Orchestration

Implemented in `src/lyra/adapters/_shared_streaming.py`.

Centralizes the edit-in-place streaming algorithm for all platform adapters.
Platform-specific behavior is injected via `PlatformCallbacks`.

### PlatformCallbacks

```python
@dataclass
class PlatformCallbacks:
    send_placeholder: Callable[[], Awaitable[tuple[Any, int | None]]]
    edit_placeholder_text: Callable[[Any, str], Awaitable[None]]
    edit_placeholder_tool: Callable[[Any, ToolSummaryRenderEvent, str], Awaitable[None]]
    send_message: Callable[[str], Awaitable[int | None]]
    send_fallback: Callable[[str], Awaitable[int | None]]
    chunk_text: Callable[[str], list[str]]
    start_typing: Callable[[], None]
    cancel_typing: Callable[[], None]
    get_msg: Callable[[str, str], str]
    placeholder_text: str
    guard_tool_on_intermediate: bool = True
```

### Lifecycle

1. Send placeholder message
2. Edit placeholder on each `RenderEvent` (debounced)
3. Deliver final text (edit placeholder or send new message for tool turns)
4. Manage typing indicator tail

---

## Outbound Adapters — Responsibilities

Each outbound adapter receives an `AsyncIterator[RenderEvent]` and decides how to render it.

**TelegramOutbound:**
- `TextRenderEvent` → `sendMessage` if `is_final`, else `⏳ text` placeholder
- `ToolSummaryRenderEvent` → `sendMessage` on first, then `editMessage` (throttled)
- `ToolSummaryRenderEvent(is_complete=True)` → final edit with ✅

**DiscordOutbound:**
- `ToolSummaryRenderEvent` → update embed in current message
- `TextRenderEvent(is_final=True)` → new message or embed continuation

**CliOutbound:**
- Colored print line by line, no edit

---

## Driver Stack

Three drivers implement `LlmProvider`:

| Driver | Backend | Streaming | Auth |
|--------|---------|-----------|------|
| `AnthropicSdkDriver` | Anthropic Messages API | ❌ buffers full response | `api_key` |
| `ClaudeCliDriver` | Claude Code subprocess | ✅ native NDJSON stream | `oauth_only` |
| `NatsLlmDriver` | Remote LLM worker over NATS | ✅ ephemeral inbox | `nats` |

### Decorator Stack

```
CircuitBreakerDecorator → SmartRoutingDecorator → RetryDecorator → Driver
```

Each decorator wraps an `LlmProvider` and implements the same protocol.
Stack assembled in `bootstrap/` during startup — not in `llm/`.

---

## Schema Versioning

Every hub↔adapter envelope carries `schema_version: int`.

- Current version defined in `SCHEMA_VERSION_*` constants
- Receiver accepts `schema_version <= expected`
- Strictly-greater versions **dropped** with ERROR log + counter
- Legacy payloads without `schema_version` default to version 1

**How to bump:**

1. Bump `SCHEMA_VERSION_<ENVELOPE>` constant
2. Update `schema_version` field default on envelope
3. Coordinate simultaneous deploy of hub + adapters
4. Verify: `grep SCHEMA_VERSION_ src/lyra/core/*.py`

---

## Implementation Status

| Component | Status |
|-----------|--------|
| `LlmProvider` protocol | ✅ Implemented |
| `LlmEvent` types | ✅ Implemented |
| `RenderEvent` types | ✅ Implemented |
| `StreamProcessor` | ✅ Implemented |
| `StreamingSession` | ✅ Implemented |
| `PlatformCallbacks` | ✅ Implemented |
| `OutboundAdapterBase` | ✅ Implemented |
| Drivers (3) | ✅ AnthropicSdk, ClaudeCli, NatsLlm |
| Decorator stack | ✅ CircuitBreaker → SmartRouting → Retry |
