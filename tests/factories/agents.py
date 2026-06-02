"""Agent and adapter mock factories for tests."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from factory.core.agent import Agent
from factory.core.auth.trust import TrustLevel
from factory.core.messaging.message import InboundMessage
from factory.core.pool import Pool
from factory.core.ports.stt import STTProtocol, TranscriptionResult
from factory.llm.base import LlmResult

if TYPE_CHECKING:
    from factory.core.messaging.message import (
        Response,
    )

__all__ = [
    "FastAgent",
    "RecordingAgent",
    "SlowAgent",
    "make_cli_pool",
    "make_config",
    "make_mock_stt",
    "make_pool",
    "make_text_message",
]


class RecordingAgent:
    """Agent that records the text it receives."""

    name = "test_agent"

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def process(
        self, msg: InboundMessage, pool: Pool, *, on_intermediate=None
    ) -> Response:
        self.calls.append(msg.text)
        return Response(content=f"reply:{msg.text}")


class SlowAgent:
    """Agent whose process() never returns within test timeouts."""

    name = "test_agent"

    def __init__(self, shutdown_event: asyncio.Event | None = None) -> None:
        self._shutdown = shutdown_event if shutdown_event else asyncio.Event()

    def is_backend_alive(self, pool_id: str) -> bool:
        return True

    async def reset_backend(self, pool_id: str) -> None:
        pass

    async def process(
        self,
        msg: InboundMessage,
        pool: Pool,
        *,
        on_intermediate=None,
    ) -> Response:
        await self._shutdown.wait()  # explicit: never completes in test
        return Response(content="done")


class FastAgent:
    """Agent that echoes the message text immediately."""

    name = "test_agent"

    async def process(
        self,
        msg: InboundMessage,
        pool: Pool,
        *,
        on_intermediate=None,
    ) -> Response:
        return Response(content=f"echo: {msg.text}")


def make_text_message(text: str = "hello") -> InboundMessage:
    from factory.core.messaging.message import TelegramMeta

    return InboundMessage(
        id="msg-text",
        platform="telegram",
        bot_id="main",
        scope_id="chat:42",
        user_id="alice",
        user_name="Alice",
        is_mention=False,
        text=text,
        text_raw=text,
        timestamp=datetime.now(timezone.utc),
        platform_meta=TelegramMeta(chat_id=42),
        trust_level=TrustLevel.TRUSTED,
    )


def make_pool(pool_id: str = "telegram:main:alice") -> Pool:
    return Pool(pool_id=pool_id, agent_name="lyra", ctx=MagicMock())


def make_config() -> Agent:
    from factory.core.agent.agent_config import ModelConfig

    return Agent(
        name="lyra",
        system_prompt="You are Lyra.",
        memory_namespace="lyra",
        llm_config=ModelConfig(),
    )


def make_mock_stt(
    result: TranscriptionResult | None = None, raises: Exception | None = None
) -> STTProtocol:
    """Return a MagicMock standing in for STTProtocol with transcribe pre-configured."""
    stt = MagicMock(spec=STTProtocol)
    if raises is not None:
        stt.transcribe = AsyncMock(side_effect=raises)
    else:
        stt.transcribe = AsyncMock(return_value=result)
    return stt


def make_cli_pool(result: str = "cli response") -> MagicMock:
    provider = MagicMock()
    provider.complete = AsyncMock(
        return_value=LlmResult(result=result, session_id="s1")
    )
    return provider
