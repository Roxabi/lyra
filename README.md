# Lyra

Personal AI agent engine — hub-and-spoke, asyncio, multi-channel.

## Architecture

```
Telegram ──┐
Discord  ──┼──▶ asyncio.Queue(100) ──▶ Hub ──▶ resolve_binding()
Signal   ──┘                                          │
                                              get_or_create_pool()
                                                      │
                                              agent.process(msg, pool)
                                                      │
                                       adapter_registry[channel].send(response)
```

Each channel adapter pushes normalized `Message` objects into a bounded bus. The hub routes them to the right agent via bindings, processes them in isolated per-user pools, and sends responses back through the adapter registry.

## Structure

```
src/lyra/core/
  message.py   — Message dataclass + MessageType enum
  pool.py      — Pool (history + asyncio.Lock per user)
  agent.py     — Agent (frozen singleton), ChannelAdapter protocol, Response
  hub.py       — Hub (bus + adapter registry + bindings)
tests/core/
  test_hub.py  — 19 tests
```

## Setup

```bash
uv venv .venv
uv pip install -e ".[dev]"
```

## Tests

```bash
.venv/bin/pytest
```

## Status

| Day | Scope | Status |
|-----|-------|--------|
| D3 | Message, Pool, Agent, Hub init + register_adapter + register_binding | ✓ done |
| D4 | `get_or_create_pool()`, run loop, `dispatch_response()`, mock end-to-end | pending |
| D5 | Telegram adapter connected to the hub | pending |
