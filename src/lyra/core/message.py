from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

# Shared user-facing fallback for unhandled agent or dispatch errors.
GENERIC_ERROR_REPLY = "Something went wrong. Please try again."


class Platform(str, Enum):
    TELEGRAM = "telegram"
    DISCORD = "discord"


@dataclass(frozen=True)
class Attachment:
    """A file or media attachment on an InboundMessage."""

    type: str  # "image" | "audio" | "video" | "file"
    url_or_bytes: str | bytes  # URL string or raw bytes
    mime_type: str
    filename: str | None = None


@dataclass(frozen=True)
class InboundMessage:
    """Normalized inbound envelope produced by all channel adapters.

    platform_meta carries platform-specific routing data. See spec platform_meta table.
    Security: trust is always 'user' from adapters — never set above adapter layer.
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
    attachments: list[Attachment] = field(default_factory=list)
    reply_to_id: str | None = None
    thread_id: str | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    locale: str | None = None
    trust: Literal["user", "system"] = "user"
    platform_meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class InboundAudio:
    """Normalized inbound audio envelope produced by all channel adapters.

    Mirrors InboundMessage for audio: adapters produce this; hub/agents consume it.
    Bus enqueue is a future concern (issue #140 follow-on).
    Security: trust is always 'user' from adapters — never set above adapter layer.
    """

    id: str
    platform: str  # "telegram" | "discord" | ...
    bot_id: str
    scope_id: str
    user_id: str
    audio_bytes: bytes
    mime_type: str
    duration_ms: int | None
    file_id: str | None
    timestamp: datetime
    trust: Literal["user", "system"] = "user"
    user_name: str = ""
    is_mention: bool = False
    platform_meta: dict = field(default_factory=dict)


@dataclass
class Response:
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_outbound(self) -> "OutboundMessage":
        """Convert to OutboundMessage for use with the typed dispatch path."""
        return OutboundMessage.from_text(self.content)


@dataclass(frozen=True)
class OutboundAudio:
    """Typed envelope for outbound audio data on the bus.

    Produced by TTS / voice pipelines; consumed by adapter render_audio().
    audio_bytes holds the raw audio payload (e.g. ogg/opus from TTS).
    """

    audio_bytes: bytes = field(repr=False)
    mime_type: str = "audio/ogg"  # e.g. "audio/ogg", "audio/mpeg"
    duration_ms: int | None = None
    caption: str | None = None
    reply_to_id: str | None = None  # platform message ID to reply to


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
    metadata: dict[str, Any] = field(default_factory=dict)

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
