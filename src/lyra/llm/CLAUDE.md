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
| `LlmClient` | `"claude-cli"` / `"nats"` | NATS request-reply via `WorkerPoolClient` + `CliNatsCodec` | multi-process (hub side) |

`ClaudeCliDriver` and `LlmClient` may share the `"claude-cli"` registry key — selection between them is determined by wiring mode at bootstrap (single-process picks `ClaudeCliDriver`, multi-process picks `LlmClient(WorkerPoolClient, CliNatsCodec)`).

`LlmClient` lives in `lyra.llm.llm_client` (this package). `LlmClient(pool, codec)` is the
3-layer composition for the NATS LLM path; the legacy per-driver `CliNatsDriver`
(formerly in `lyra.llm.drivers.cli_nats`) was deleted in #1281. The legacy `NatsLlmClient`
(formerly in `lyra.nats`) was deleted in #1278.

## LlmClient + LlmCodec layering

The NATS LLM driver is a 3-layer composition (since #1278):

```
LlmClient (lyra.llm.llm_client)
   ├─ pool: WorkerPoolClient (lyra.transport.worker_pool_client)
   │     └─ transport: NatsTransport (lyra.transport.nats_request_response)
   └─ codec: LlmCodec (lyra.llm.codec)
```

- `LlmClient`: implements `LlmProvider`; orchestrates encode → pool → decode.
- `LlmCodec`: pure, no I/O. `encode(text, model_cfg, system_prompt, messages, *, stream)`
  → bytes payload + trace_id. `decode(result, trace_id)` → LlmResult.
  `decode_chunk(result)` → LlmEvent (TextLlmEvent | ResultLlmEvent | None).
- `WorkerPoolClient`: routing + CB + heartbeat — domain-agnostic, see `lyra.transport`.

CB is enforced at the **pool** layer (since #1278). Wiring sites wrap `LlmClient` with
`RetryDecorator` only — do NOT add `CircuitBreakerDecorator` (reserved for `ClaudeCliDriver`
which has no built-in CB).

## Timeout responsibility

`LlmClient` does **not** enforce a per-turn wall-clock deadline. This is intentional.

| Layer | What is guaranteed | What is NOT guaranteed |
|-------|-------------------|----------------------|
| `NatsTransport` | Per-chunk liveness (`default_timeout=300s`) — no silent hangs between chunks | Upper bound on total turn duration |
| `LlmClient` | Nothing beyond what the transport enforces | Any turn-level SLA |

Per-turn wall-clock is a scheduling policy; the consumer defines what a "turn" is and
what SLA applies. Wrap calls in `asyncio.timeout` when a deadline is required:

```python
async with asyncio.timeout(budget_seconds):
    async for event in provider.stream(...):
        ...
```

Historical: `CliNatsDriver` (deleted in #1281) inherited `max_total_duration=1800s` from
`NatsDriverBase`; that responsibility now belongs to the caller.

## Decorator stack

```
CircuitBreakerDecorator → RetryDecorator → Driver
```

Stack assembled in `bootstrap/`, not in `llm/`. Order matters: circuit-breaker wraps
outermost, retry wraps the driver.

`LlmClient` carries its own CB via `WorkerPoolClient`; at wiring sites it is wrapped only
by `RetryDecorator`. `CircuitBreakerDecorator` is reserved for `ClaudeCliDriver`.

## LlmEvent

`LlmEvent` union defined in `lyra.core.messaging.events` (text, thinking, tool-use
deltas, tool-result, terminal result). Read source for current members.

Import from `lyra.core.messaging.events` — `lyra.llm` does **not** re-export these
(would obscure the canonical location from `import-linter`).

## SmartRoutingConfig

`SmartRoutingConfig` lives in `lyra.core.agent.agent_config` (¬`llm/smart_routing.py` — that file
does not exist). Validator rejects `enabled = true` on all backends. Keep `enabled = false` (default).

## ProviderRegistry (`registry.py`)

Dict-based: `register(backend, driver)` / `get(backend)`. Backends: `"claude-cli"`,
`"nats"`. `get()` raises `KeyError` for unknown backends.

## LlmUnavailableError

Defined in `lyra.core.ports.llm` (¬`llm/errors.py` — that file does not exist). Import from the
canonical path in all new code.

## Constraints

- `llm/` never imports from `adapters/`, `commands/`, or any framework (aiogram, discord).
- Decorator stack construction belongs in `bootstrap/`, not here.
- `capabilities["streaming"]` is for callers, not drivers.
