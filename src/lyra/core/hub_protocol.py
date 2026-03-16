"""Protocol types for the hub: ChannelAdapter, RoutingKey, Binding."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, NamedTuple, Protocol

from .message import (
    InboundAudio,
    InboundMessage,
    OutboundAttachment,
    OutboundAudio,
    OutboundAudioChunk,
    OutboundMessage,
    Platform,
)
from .trust import TrustLevel


class ChannelAdapter(Protocol):
    """Interface every channel adapter must implement.

    Security contract: adapters are responsible for verifying the identity
    of the sender (e.g. via platform token, signed webhook, or session)
    before constructing an InboundMessage. The hub trusts ``InboundMessage.user_id``
    as the authenticated sender identity (used for rate limiting and pairing) and
    ``InboundMessage.scope_id`` as the conversation scope (used for pool routing).
    Never derive either from unverified inbound data.
    """

    def normalize(self, raw: Any) -> InboundMessage: ...

    def normalize_audio(
        self, raw: Any, audio_bytes: bytes, mime_type: str, *, trust_level: TrustLevel
    ) -> InboundAudio: ...

    async def send(
        self, original_msg: InboundMessage, outbound: OutboundMessage
    ) -> None: ...

    async def send_streaming(
        self,
        original_msg: InboundMessage,
        chunks: AsyncIterator[str],
        outbound: OutboundMessage | None = None,
    ) -> None:
        """Stream response to the channel with edit-in-place.

        When *outbound* is provided, adapters write the platform message ID
        to ``outbound.metadata["reply_message_id"]`` after sending the
        placeholder, mirroring the contract of :meth:`send`.
        """
        ...

    async def render_audio(self, msg: OutboundAudio, inbound: InboundMessage) -> None:
        """Send an outbound audio envelope (voice note) to the channel."""
        ...

    async def render_audio_stream(
        self,
        chunks: AsyncIterator[OutboundAudioChunk],
        inbound: InboundMessage,
    ) -> None:
        """Stream outbound audio chunks to the channel.

        Adapters buffer chunks via ``buffer_audio_chunks()`` and send when
        complete. Mirrors ``send_streaming()`` for text.
        """
        ...

    async def render_voice_stream(
        self,
        chunks: AsyncIterator[OutboundAudioChunk],
        inbound: InboundMessage,
    ) -> None:
        """Stream TTS audio to an active voice session (Discord voice channel).

        Adapters that support voice playback implement this method.
        """
        ...

    async def render_attachment(
        self, msg: OutboundAttachment, inbound: InboundMessage
    ) -> None:
        """Send an outbound attachment (image/video/document/file) to the channel."""
        ...


class RoutingKey(NamedTuple):
    """Routing key: (platform, bot_id, scope_id). Use scope_id='*' for wildcard."""

    platform: Platform
    bot_id: str
    scope_id: str

    def to_pool_id(self) -> str:
        """Canonical pool ID: '{platform.value}:{bot_id}:{scope_id}'.

        Use this method as the single source of truth for pool ID format (ADR-001 §4).
        Never construct the pool ID string inline.
        """
        return f"{self.platform.value}:{self.bot_id}:{self.scope_id}"


@dataclass(frozen=True)
class Binding:
    agent_name: str
    pool_id: str
