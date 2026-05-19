# src/lyra/llm/ — LLM Providers and Drivers

## Purpose

`llm/` is the only package that talks to external LLM backends. Everything else
interacts with LLMs through the `LlmProvider` protocol.

## LlmProvider protocol

SSoT: `lyra.core.ports.llm`. `base.py` is a backward-compatibility shim — import from
`lyra.core.ports.llm` directly in all new code.

`stream()` is duck-typed optional — callers check `hasattr(provider, "stream")`. Do
not add it to the Protocol until all drivers implement it.

`LlmResult`: check `.ok` before using `.result`. `error` is a non-empty string on failure.
`retryable=False` = caller must NOT retry (quota, bad key). Default `True` = transient.

## Drivers

| Driver | Registry key | Transport | Wiring mode |
|--------|-------------|-----------|-------------|
| `ClaudeCliDriver` | `"claude-cli"` | in-process (`CliPool` subprocess) | single-process |
| `CliNatsDriver` | `"claude-cli"` | NATS request-reply → clipool worker | multi-process (hub side) |
| `NatsLlmClient` | `"nats"` | NATS request-reply → llmCLI worker | multi-process (hub side) |

`ClaudeCliDriver` and `CliNatsDriver` share the same `"claude-cli"` registry key — the two are interchangeable by wiring mode, not by registry key.

`NatsLlmClient` lives in `lyra.nats`, **not** in `llm/` — cross-package gotcha. Replaces deleted `NatsLlmDriver` (#1119).

## Decorator stack

```
CircuitBreakerDecorator → SmartRoutingDecorator → RetryDecorator → Driver
```

Stack assembled in `bootstrap/`, not in `llm/`. Order matters: circuit-breaker wraps
outermost, retry wraps the driver.

`NatsLlmClient` carries its own `NatsCircuitBreaker`; at wiring sites it is wrapped only
by `RetryDecorator`. `CircuitBreakerDecorator` is reserved for `ClaudeCliDriver`.

## LlmEvent

`LlmEvent` union defined in `lyra.core.messaging.events` (text, thinking, tool-use
deltas, tool-result, terminal result). Read source for current members.

Import from `lyra.core.messaging.events` — `lyra.llm` does **not** re-export these
(would obscure the canonical location from `import-linter`).

## smart_routing.py

5-line stub kept for backward import compatibility — all classifier/decorator logic
removed in #666. Only `SmartRoutingConfig` remains; validator rejects `enabled = true`
on all backends. Keep `enabled = false` (default).

## ProviderRegistry (`registry.py`)

Dict-based: `register(backend, driver)` / `get(backend)`. Backends: `"claude-cli"`,
`"nats"`. `get()` raises `KeyError` for unknown backends.

## errors.py

Shim — re-exports `LlmUnavailableError` from `lyra.core.ports.llm`. Useful for import
resolution; prefer the canonical path in new code.

## Constraints

- `llm/` never imports from `adapters/`, `commands/`, or any framework (aiogram, discord).
- Decorator stack construction belongs in `bootstrap/`, not here.
- `capabilities["streaming"]` is for callers, not drivers.
