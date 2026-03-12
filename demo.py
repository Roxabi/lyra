"""Minimal demo: hub + echo agent + fake adapter — no tokens needed."""

import asyncio
from datetime import datetime, timezone

from lyra.core.agent import Agent, AgentBase
from lyra.core.auth import TrustLevel
from lyra.core.hub import Hub
from lyra.core.message import (
    Message,
    MessageType,
    Platform,
    Response,
    TelegramContext,
    TextContent,
)
from lyra.core.pool import Pool


class EchoAgent(AgentBase):
    """Echoes back whatever the user sends."""

    async def process(self, msg: Message, pool: Pool) -> Response:
        content = msg.content
        text = content.text if isinstance(content, TextContent) else str(content)
        return Response(content=f"Echo: {text}")


class FakeAdapter:
    """Prints responses to stdout instead of sending to a platform."""

    def __init__(self) -> None:
        self.responses: list[Response] = []

    async def send(self, original_msg: Message, response: Response) -> None:
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

    # Start hub consumer
    hub_task = asyncio.create_task(hub.run())

    # Simulate messages
    for text in ["Hello Lyra!", "How does routing work?", "Goodbye"]:
        msg = Message.from_adapter(
            platform=Platform.TELEGRAM,
            bot_id="main",
            user_id="tg:user:42",
            user_name="Mickael",
            content=TextContent(text=text),
            type=MessageType.TEXT,
            timestamp=datetime.now(timezone.utc),
            trust_level=TrustLevel.TRUSTED,
            platform_context=TelegramContext(chat_id=123),
        )
        print(f"  -> {text}")
        await hub.bus.put(msg)

    # Let the hub process all messages
    await hub.bus.join()

    print(f"\nDone — {len(adapter.responses)} messages routed successfully.")
    hub_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
