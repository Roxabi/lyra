"""Minimal demo: hub + echo agent + fake adapter — no tokens needed."""

import asyncio
from datetime import datetime, timezone

from factory.core.message import InboundMessage, Platform, Response

from factory.core.agent import Agent, AgentBase
from factory.core.auth import TrustLevel
from factory.core.hub import Hub
from factory.core.pool import Pool


class EchoAgent(AgentBase):
    """Echoes back whatever the user sends."""

    async def process(self, msg: InboundMessage, pool: Pool) -> Response:
        return Response(content=f"Echo: {msg.text}")


class FakeAdapter:
    """Prints responses to stdout instead of sending to a platform."""

    def __init__(self) -> None:
        self.responses: list[Response] = []

    async def send(self, original_msg: InboundMessage, response: Response) -> None:
        self.responses.append(response)
        print(f"  <- {response.content}")


async def main() -> None:
    hub = Hub()

    # Wire up
    agent = EchoAgent(Agent(name="echo", system_prompt="", memory_namespace="test"))
    hub.register_agent(agent)

    adapter = FakeAdapter()
    hub.register_adapter(Platform.TELEGRAM, "main", adapter)
    hub.register_binding(Platform.TELEGRAM, "main", "*", "echo", "telegram:main:*")

    # Start bus feeders + hub consumer
    await hub.inbound_bus.start()
    hub_task = asyncio.create_task(hub.run())

    # Simulate messages
    for text in ["Hello Lyra!", "How does routing work?", "Goodbye"]:
        msg = InboundMessage(
            id=f"demo-{text[:5]}",
            platform="telegram",
            bot_id="main",
            scope_id="chat:123",
            user_id="tg:user:42",
            user_name="Mickael",
            is_mention=True,
            text=text,
            text_raw=text,
            timestamp=datetime.now(timezone.utc),
            platform_meta={"chat_id": 123},
            trust_level=TrustLevel.TRUSTED,
        )
        print(f"  -> {text}")
        await hub.inbound_bus.put(Platform.TELEGRAM, msg)

    # Let the hub process all messages
    while hub.inbound_bus.staging_qsize() > 0:
        await asyncio.sleep(0)

    print(f"\nDone — {len(adapter.responses)} messages routed successfully.")
    hub_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
