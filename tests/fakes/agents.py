"""Agent and adapter test doubles for the Lyra test suite."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from lyra.core.auth.trust import TrustLevel
from lyra.core.messaging.message import InboundMessage, OutboundMessage

if __name__ == "__main__":
    # Prevent direct execution; these are test-only doubles.
    raise RuntimeError("test doubles must not be executed as __main__")


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
        pending: Any = None,
    ) -> InboundMessage:
        raise NotImplementedError

    async def send(
        self, original_msg: InboundMessage, outbound: OutboundMessage
    ) -> None:
        pass

    async def send_streaming(
        self,
        original_msg: InboundMessage,
        events: AsyncIterator[Any],
        outbound: OutboundMessage | None = None,
    ) -> None:
        pass

    async def render_audio(self, msg: Any, inbound: InboundMessage) -> None:
        pass

    async def render_audio_stream(
        self, chunks: AsyncIterator[Any], inbound: InboundMessage
    ) -> None:
        pass

    async def render_voice_stream(
        self, chunks: AsyncIterator[Any], inbound: InboundMessage
    ) -> None:
        pass

    async def render_attachment(self, msg: Any, inbound: InboundMessage) -> None:
        pass


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


__all__ = [
    "FakeSTT",
    "FakeTranscription",
    "MockAdapter",
]
