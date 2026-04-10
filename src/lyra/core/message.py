from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Literal

from lyra.core.audio_payload import AudioPayload
from lyra.core.trust import TrustLevel

if TYPE_CHECKING:
    from lyra.core.commands.command_parser import CommandContext

# Shared user-facing fallback for unhandled agent or dispatch errors.
GENERIC_ERROR_REPLY = "Something went wrong. Please try again."

SCHEMA_VERSION_INBOUND_MESSAGE = 1
SCHEMA_VERSION_OUTBOUND_MESSAGE = 1


class Platform(str, Enum):
    TELEGRAM = "telegram"
    DISCORD = "discord"


@dataclass(frozen=True)
class RoutingContext:
    """Immutable routing envelope carried from inbound to outbound.

    Populated by adapters during normalize(). Propagated through
    InboundMessage → Response/OutboundMessage via Hub dispatch.
    Verified by OutboundDispatcher before delivery — platform + bot_id
    must match the dispatcher's own identity.
    """

    platform: str  # "telegram" | "discord"
    bot_id: str  # bot identifier ("main")
    scope_id: str  # canonical routing scope (chat:123, channel:456, thread:789)
    thread_id: str | None = None
    # The inbound message ID to reply to. Reserved for future outbound
    # reply threading; not yet read by adapters (they use platform_meta).
    reply_to_message_id: str | None = None
    platform_meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Attachment:
    """A file or media attachment on an InboundMessage.

    url_or_path_or_bytes stores platform-specific references:
    - Discord: direct CDN URL (str) — fetchable with HTTP GET.
    - Telegram: prefixed file_id (str, ``"tg:file_id:{id}"``) — resolve via
      Bot API ``getFile``. Detect with
      ``url_or_path_or_bytes.startswith("tg:file_id:")``.
    - Local filesystem path (str, e.g. ``"/tmp/tmpXXX.ogg"``) — for audio
      downloaded by adapters before normalization.
    - Raw bytes (bytes) — for pre-downloaded media (future).
    """

    type: str  # "image" | "audio" | "video" | "file"
    url_or_path_or_bytes: str | bytes  # URL, local path, or raw bytes
    mime_type: str
    filename: str | None = None


@dataclass(frozen=True)
class InboundMessage:
    """Normalized inbound envelope produced by all channel adapters.

    platform_meta carries platform-specific routing data. See spec platform_meta table.
    Security (C3): adapters set trust_level=PUBLIC; Hub overwrites via
    _resolve_message_trust() (ResolveTrustMiddleware) before the pipeline runs.
    Bot-authored messages are filtered by adapters before normalize() is called.
    """

    id: str
    platform: str  # "telegram" | "discord" | ...
    bot_id: str
    scope_id: str  # canonical routing scope (computed by adapter)
    user_id: str
    user_name: str
    is_mention: bool
    text: str  # normalized plain text (markup stripped)
    text_raw: str  # original text with platform markup
    trust_level: TrustLevel
    schema_version: int = 1
    is_admin: bool = False
    roles: tuple[str, ...] = ()
    attachments: list[Attachment] = field(default_factory=list)
    reply_to_id: str | None = None
    thread_id: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    locale: str | None = None
    # Whisper-detected spoken language (e.g. "fr") — distinct from locale
    language: str | None = None
    platform_meta: dict = field(default_factory=dict)
    routing: RoutingContext | None = None
    command: CommandContext | None = None
    modality: Literal["text", "voice"] | None = None
    # Set to True by processors that have already enriched text with trusted context.
    # Agents use this flag to decide whether to wrap plain text in <user_message> tags.
    processor_enriched: bool = False
    # Audio payload — populated when modality == "voice" (#534).
    # Stripped to None by the STT pipeline stage after successful transcription.
    audio: AudioPayload | None = None


@dataclass
class Response:
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    routing: RoutingContext | None = None
    intermediate: bool = False  # True for ⏳ intermediate turns
    audio: "OutboundAudio | None" = None
    speak: bool = False  # True → TTS the response (set by agent on /voice commands)

    def to_outbound(self) -> "OutboundMessage":
        """Convert to OutboundMessage for use with the typed dispatch path."""
        outbound = OutboundMessage.from_text(self.content)
        outbound.routing = self.routing
        outbound.intermediate = self.intermediate
        return outbound


@dataclass(frozen=True)
class OutboundAudio:
    """Typed envelope for outbound audio data on the bus.

    Produced by TTS / voice pipelines; consumed by adapter render_audio().
    audio_bytes holds the raw audio payload (e.g. ogg/opus from TTS).
    """

    audio_bytes: bytes = field(repr=False)
    mime_type: str = "audio/ogg"  # e.g. "audio/ogg", "audio/mpeg"
    duration_ms: int | None = None
    waveform_b64: str | None = None  # 256-byte amplitude array, base64
    caption: str | None = None
    reply_to_id: str | None = None  # platform message ID to reply to


@dataclass(frozen=True)
class OutboundAttachment:
    """Typed envelope for outbound file/image/video/document attachments.

    Produced by agents or pipelines; consumed by adapter render_attachment().
    Mirrors OutboundAudio for non-audio media. Adapters dispatch to
    platform-specific send methods based on the type field.

    Type semantics:
      - "image": rendered inline (Telegram send_photo, Discord embed)
      - "video": rendered inline (Telegram send_video)
      - "document": file with preview (Telegram send_document, shown as doc)
      - "file": opaque binary (Telegram send_document, no preview hint)
    """

    data: bytes = field(repr=False)
    type: Literal["image", "video", "document", "file"]
    mime_type: str
    filename: str | None = None
    caption: str | None = None
    reply_to_id: str | None = None  # platform message ID to reply to


@dataclass(frozen=True)
class OutboundAudioChunk:
    """A single chunk of streamed outbound audio.

    Produced incrementally by TTS pipelines; consumed by adapter
    render_audio_stream(). Adapters buffer chunks and send when is_final=True.
    """

    chunk_bytes: bytes = field(repr=False)
    session_id: str
    chunk_index: int
    is_final: bool = False
    mime_type: str = "audio/ogg"
    caption: str | None = None
    reply_to_id: str | None = None


# ── Outbound envelope ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Button:
    """A button to display below a message."""

    text: str
    callback_data: str


@dataclass(frozen=True)
class CodeBlock:
    """A fenced code block content part."""

    code: str
    language: str | None = None


@dataclass(frozen=True)
class MediaPart:
    """A media attachment content part for outbound messages.

    Distinct from the inbound Attachment type (which carries raw bytes/URL
    for received media). MediaPart is for outbound OutboundMessage.content[].
    """

    url: str
    media_type: str
    caption: str | None = None


# ContentPart: plain text, code block, or media attachment.
ContentPart = str | CodeBlock | MediaPart


@dataclass
class OutboundMessage:
    """Normalized output envelope produced by the hub/agents.

    Adapters consume this through their send() method, owning all
    platform-specific translation (MarkdownV2 escaping, chunking, button
    construction) internally.

    Not frozen — metadata["reply_message_id"] is written by adapters after send.
    edit_id and is_final are reserved for future streaming unification.
    """

    content: list[ContentPart]
    buttons: list[Button] = field(default_factory=list)
    edit_id: str | None = None
    is_final: bool = True
    intermediate: bool = False  # True → typing continues after send
    metadata: dict[str, Any] = field(default_factory=dict)
    routing: RoutingContext | None = None
    schema_version: int = 1

    @classmethod
    def from_text(cls, text: str) -> "OutboundMessage":
        """Convenience constructor: single plain-text content part."""
        return cls(content=[text])

    def to_text(self) -> str:
        """Flatten content parts to a plain string for adapter rendering.

        str parts → verbatim; CodeBlock → fenced code block; Attachment → URL caption.
        """
        parts: list[str] = []
        for part in self.content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, CodeBlock):
                lang = part.language or ""
                parts.append(f"```{lang}\n{part.code}\n```")
            else:
                # Attachment
                caption = f" — {part.caption}" if part.caption else ""
                parts.append(f"{part.url}{caption}")
        return "\n".join(parts)
