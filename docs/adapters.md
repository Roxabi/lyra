# Adapters — How to add a channel

A channel adapter connects an external messaging platform (Telegram, Discord, Signal…) to the Lyra hub. It normalizes incoming messages into the unified `Message` format and sends responses back through the platform's API.

<!-- TODO: write this doc after D5 (Telegram adapter connected to the hub) -->

## Interface

```python
class ChannelAdapter(Protocol):
    async def send(self, original_msg: Message, response: Response) -> None: ...
```

<!-- TODO: document the full adapter lifecycle:
  - startup: register with hub.register_adapter()
  - receive: normalize platform event → Message → await hub.bus.put(msg)
  - backpressure: send acknowledgment if bus is full, then await hub.bus.put()
  - send: implement send() to deliver response back to the platform
-->

## Reference implementation

<!-- TODO: add Telegram adapter walkthrough once D5 is merged -->

## Backpressure

<!-- TODO: document the bounded queue (maxsize=100) + acknowledgment pattern -->
