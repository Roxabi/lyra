# src/lyra/llm/ — LLM Providers and Drivers

## Purpose

`llm/` defines the `LlmProvider` protocol and its concrete driver implementations.
It is the only package that talks to external LLM backends. Everything else in the
system interacts with LLMs only through this interface.

## LlmProvider protocol (`base.py`)

```python
class LlmProvider(Protocol):
    capabilities: dict[str, Any]

    async def complete(pool_id, text, model_cfg, system_prompt, *, messages, on_intermediate) -> LlmResult: ...
    def is_alive(pool_id) -> bool: ...
    async def stream(pool_id, text, model_cfg, system_prompt, *, messages) -> AsyncIterator[LlmEvent]: ...
```

`stream()` is **duck-typed optional** — callers check `hasattr(provider, "stream")`.
Existing drivers that do not implement it are not broken. Do not add `stream()` to
the Protocol base until all drivers implement it.

`LlmResult` fields: `result` (text), `session_id`, `error`, `retryable`, `warning`,
`user_message`. Check `result.ok` before using `result.result`.

## Drivers

Two concrete drivers in `drivers/`:

| Driver | Backend | `capabilities["streaming"]` | `capabilities["auth"]` |
|--------|---------|---------------------------|----------------------|
| `AnthropicSdkDriver` | Anthropic Messages API (HTTP) | `False` — buffers full response | `"api_key"` |
| `ClaudeCliDriver` | Claude Code subprocess (`CliPool`) | `True` — native NDJSON stream | `"oauth_only"` |


## Decorator stack

```
CircuitBreakerDecorator → SmartRoutingDecorator → RetryDecorator → Driver
```

Each decorator wraps an `LlmProvider` and implements the same protocol.
The stack is assembled in `bootstrap/` during startup — not in `llm/`.

`SmartRoutingDecorator` (`smart_routing.py`) selects a cheaper model for trivial
messages and upgrades to a more capable model for complex ones.

## LlmEvent types (defined in `core/events.py`)

Events emitted by streaming drivers. The types live in `lyra.core.events` so
that `core/` can consume them without importing `llm/` — preserving the
unidirectional `llm → core` dependency (enforced by `import-linter`).

| Event | Purpose |
|-------|---------|
| `TextLlmEvent(text)` | A chunk of streamed text |
| `ToolUseLlmEvent(tool_name, tool_id, input)` | LLM called a tool |
| `ResultLlmEvent(is_error, duration_ms, cost_usd)` | Turn complete (always last) |

`LlmEvent = TextLlmEvent | ToolUseLlmEvent | ResultLlmEvent` — use this union type
for annotations. All event classes are `frozen=True` — never mutate after construction.

Import canonically from `lyra.core.events`. The `lyra.llm` package does **not**
re-export these types — doing so would make the canonical location invisible to
tooling (`import-linter` checks statements, not re-exports). `cost_usd` is always
`None` for `ClaudeCliDriver` (not present in its NDJSON output).

## SmartRouting (`smart_routing.py`)

`ComplexityClassifier.classify(text)` returns `(Complexity, reason)` using zero-cost
heuristics (regex + word count). Complexity levels: `TRIVIAL`, `SIMPLE`, `MODERATE`,
`COMPLEX`.

Smart routing only works with `backend = "anthropic-sdk"`. It is incompatible with
`backend = "claude-cli"` because the CLI driver controls its own session and model
selection internally.

Configure in agent TOML under `[agent.smart_routing]`. Default: `enabled = false`.

## ProviderRegistry (`registry.py`)

A simple dict-based registry: `register(backend, driver)` and `get(backend)`.
Backends are registered by name: `"claude-cli"`, `"anthropic-sdk"`.
`get()` raises `KeyError` for unknown backends — callers must handle this.

## Conventions

- Drivers never import from `adapters/` or `commands/`.
- `LlmResult.error` is a non-empty string on failure. Always check `.ok` first.
- `retryable=False` means the caller must NOT retry (e.g. quota exhausted, bad key).
  Default is `True` (transient failures are retriable).
- No framework imports (aiogram, discord, anthropic) in `core/events.py`.
- `on_intermediate` in `complete()` is accepted for protocol compliance but ignored
  by drivers that buffer the full response — this is intentional.

## What NOT to do

- Do NOT import `Hub`, `Pool`, or any adapter in `llm/`.
- Do NOT add platform-specific logic to any driver.
- Do NOT check `capabilities["streaming"]` inside a driver — that field is for callers.
- Do NOT add new fields to `LlmEvent` subclasses without checking all consumers
  (especially `StreamProcessor` in `core/`).
- Do NOT mutate `LlmEvent` objects after construction — they are frozen dataclasses.
- Do NOT enable smart routing with `backend = "claude-cli"` — it is silently ignored
  and will produce unexpected behaviour.
- Do NOT construct the decorator stack in `llm/` — that belongs in `bootstrap/`.
