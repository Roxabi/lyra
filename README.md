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

## Docs

- [Architecture](docs/ARCHITECTURE.md)
- [Roadmap](docs/ROADMAP.md)
- [Vision](docs/vision.md)
- [Getting Started](docs/GETTING-STARTED.md)

## Setup

```bash
uv sync
```

## Tests

```bash
uv run pytest
```
