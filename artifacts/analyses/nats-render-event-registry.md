# NATS RenderEvent Type Registry — Implicit Pair Problem

**Status:** Backlog refactor  
**Discovered:** 2026-04-01 during Discord bug investigation  
**Root cause of:** Bugs where tool use embeds and final messages were silently dropped on Discord in NATS mode

---

## Problem

In NATS three-process mode, streaming responses cross a process boundary. Two modules handle the wire encoding:

| Module | Side | Role |
|--------|------|------|
| `lyra/nats/nats_channel_proxy.py` | Hub | Serializes `RenderEvent` → JSON chunks on NATS |
| `lyra/adapters/nats_outbound_listener.py` | Adapter | Deserializes JSON chunks → `RenderEvent`, calls `adapter.send_streaming()` |

These two are a **semantic pair** — they must agree on which event types exist and how to encode/decode them. But they live in different packages with no shared registry, no shared codec, and no compile-time link between them.

### What silently breaks when a new event type is added

`NatsChannelProxy.send_streaming()` assigns `event_type` like this:

```python
event_type = (
    "text" if isinstance(event, TextRenderEvent) else "tool_summary"
)
```

`NatsOutboundListener._events()` reconstructed events like this (before the fix):

```python
if event_type == "text":
    yield TextRenderEvent(**payload)
if is_done:
    break            # ← also broke on tool_summary's done=True
```

`tool_summary` was added to the serializer but never added to the deserializer. Result:

1. **Tool embeds never shown** — `ToolSummaryRenderEvent` silently dropped
2. **Final message never shown** — the final `ToolSummaryRenderEvent(is_complete=True)` had `done=True`, which terminated the loop before `TextRenderEvent(is_final=True)` arrived

The non-NATS path (direct in-process call) never had this problem because it passes the raw `AsyncIterator[RenderEvent]` directly, with no serialization layer.

---

## Why it's fragile

- **No shared source of truth** — event types are string literals (`"text"`, `"tool_summary"`, `"stream_end"`) defined independently in two files
- **No exhaustiveness check** — adding `FooRenderEvent` to `StreamProcessor` + `NatsChannelProxy` compiles and runs fine; the listener silently drops it
- **Distance** — serializer is in `lyra/nats/`, deserializer is in `lyra/adapters/`; they're not obviously related when reading either file
- **`done` semantics coupled to event type** — the termination condition (`if is_done: break`) only makes sense for `text` and `stream_end`, but was applied to all event types

---

## Proposed fix

Extract a shared `NatsRenderEventCodec` (or similar) into `lyra/nats/` alongside `NatsChannelProxy`. It owns:

1. **Serialization** — `encode(event: RenderEvent) -> dict`
2. **Deserialization** — `decode(event_type: str, payload: dict) -> RenderEvent | None`
3. **Termination logic** — `is_terminal(event_type: str, is_done: bool) -> bool`

Both `NatsChannelProxy` and `NatsOutboundListener` import from it. Adding a new `RenderEvent` subclass means updating the codec once — both sides stay in sync automatically.

### Sketch

```python
# lyra/nats/render_event_codec.py

from lyra.core.render_events import (
    FileEditSummary, RenderEvent, SilentCounts,
    TextRenderEvent, ToolSummaryRenderEvent,
)

def encode(event: RenderEvent) -> tuple[str, dict, bool]:
    """Return (event_type, payload, is_done)."""
    if isinstance(event, TextRenderEvent):
        return "text", dataclasses.asdict(event), event.is_final
    if isinstance(event, ToolSummaryRenderEvent):
        return "tool_summary", _encode_tool_summary(event), event.is_complete
    raise TypeError(f"Unknown RenderEvent type: {type(event)}")

def decode(event_type: str, payload: dict) -> RenderEvent | None:
    """Return reconstructed RenderEvent, or None for terminal sentinel."""
    if event_type == "text":
        return TextRenderEvent(**payload)
    if event_type == "tool_summary":
        return _decode_tool_summary(payload)
    if event_type == "stream_end":
        return None
    log.warning("NatsRenderEventCodec: unknown event_type=%r — dropping", event_type)
    return None

def is_terminal(event_type: str, is_done: bool) -> bool:
    """True when the stream should stop after this chunk."""
    return event_type == "stream_end" or (event_type == "text" and is_done)
```

`NatsChannelProxy` calls `encode()` instead of the inline isinstance chain.  
`NatsOutboundListener._events()` calls `decode()` and `is_terminal()` instead of the manual string checks.

---

## Scope

| File | Change |
|------|--------|
| `lyra/nats/render_event_codec.py` | **New** — codec with encode/decode/is_terminal |
| `lyra/nats/nats_channel_proxy.py` | Use `codec.encode()` |
| `lyra/adapters/nats_outbound_listener.py` | Use `codec.decode()` + `codec.is_terminal()` |

3 files, low risk. The existing hotfix (inline decode in `nats_outbound_listener.py`) is correct — this refactor makes it permanent and safe for future event types.

---

## Non-goals

- No changes to `StreamProcessor`, `RenderEvent` types, or adapter `send_streaming()` implementations
- No changes to the non-NATS direct path
