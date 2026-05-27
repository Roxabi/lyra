"""Agent and adapter mock factories for tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

from lyra.core.agent import Agent
from lyra.core.auth.trust import TrustLevel
from lyra.core.messaging.message import InboundMessage, OutboundMessage
from lyra.core.pool import Pool
from lyra.core.ports.stt import STTProtocol, TranscriptionResult
from lyra.llm.base import LlmResult

if TYPE_CHECKING:
    from lyra.core.messaging.message import (
        OutboundAttachment,
        OutboundAudio,
        OutboundAudioChunk,
        Response,
    )
    from lyra.core.messaging.render_events import RenderEvent

__all__ = [
    "FastAgent",
    "FakeSTT",
    "MockAdapter",
    "RecordingAgent",
    "SlowAgent",
    "make_audio_message",
    "make_cli_pool",
    "make_config",
    "make_mock_stt",
    "make_pool",
    "make_text_message",
]


class MockAdapter:
    """Typed ChannelAdapter test double — implements the full protocol."""

    def normalize(self, raw: Any) -> InboundMessage:
        raise NotImplementedError

    def normalize_audio(
        self,
        raw: Any,
        audio_bytes: bytes,
        mime_type: str,
        *,
        trust_level: TrustLevel,
    ) -> InboundMessage:
        raise NotImplementedError

    async def send(
        self, original_msg: InboundMessage, outbound: OutboundMessage
    ) -> None:
        pass

    async def send_streaming(
        self,
        original_msg: InboundMessage,
        events: AsyncIterator[RenderEvent],
        outbound: OutboundMessage | None = None,
    ) -> None:
        pass

    async def render_audio(self, msg: OutboundAudio, inbound: InboundMessage) -> None:
        pass

    async def render_audio_stream(
        self, chunks: AsyncIterator[OutboundAudioChunk], inbound: InboundMessage
    ) -> None:
        pass

    async def render_voice_stream(
        self, chunks: AsyncIterator[OutboundAudioChunk], inbound: InboundMessage
    ) -> None:
        pass

    async def render_attachment(
        self, msg: OutboundAttachment, inbound: InboundMessage
    ) -> None:
        pass


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


@dataclass
class FakeTranscription:
    text: str
    language: str = "en"
    duration_seconds: float = 2.5


class FakeSTT:
    def __init__(self, text: str = "Hello world") -> None:
        self._text = text

    async def transcribe(self, audio, mime):
        return FakeTranscription(text=self._text)


def make_audio_message(url: str) -> InboundMessage:
    from lyra.core.messaging.message import Attachment, TelegramMeta

    return InboundMessage(
        id="msg-audio",
        platform="telegram",
        bot_id="main",
        scope_id="chat:42",
        user_id="alice",
        user_name="Alice",
        is_mention=False,
        text="",
        text_raw="",
        attachments=[
            Attachment(type="audio", url_or_path_or_bytes=url, mime_type="audio/ogg"),
        ],
        timestamp=datetime.now(timezone.utc),
        platform_meta=TelegramMeta(chat_id=42),
        trust_level=TrustLevel.TRUSTED,
    )


def make_text_message(text: str = "hello") -> InboundMessage:
    from lyra.core.messaging.message import TelegramMeta

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
    from lyra.core.agent.agent_config import ModelConfig

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
