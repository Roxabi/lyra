from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel

from lyra.core.auth import TrustLevel

# Shared user-facing fallback for unhandled agent or dispatch errors.
GENERIC_ERROR_REPLY = "Something went wrong. Please try again."


class Platform(str, Enum):
    TELEGRAM = "telegram"
    DISCORD = "discord"
    CLI = "cli"


class MessageType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    COMMAND = "command"
    SYSTEM = "system"


class TextContent(BaseModel):
    """Plain text message content."""

    text: str


class ImageContent(BaseModel):
    """Image message content — URL or base64 data."""

    url: str
    caption: str | None = None


class AudioContent(BaseModel):
    """Audio message content — URL or base64 data."""

    url: str
    duration_seconds: float | None = None
    file_id: str | None = None  # Platform file ID (e.g. Telegram file_id) for debugging


MessageContent = TextContent | ImageContent | AudioContent


def extract_text(msg: "Message") -> str:
    """Extract plain text from a Message, regardless of content type."""
    content: MessageContent | str = msg.content
    if isinstance(content, str):
        return content
    if isinstance(content, TextContent):
        return content.text
    url = getattr(content, "url", str(content))
    caption = getattr(content, "caption", None)
    content_type = type(content).__name__.replace("Content", "").lower()
    suffix = f" — {caption}" if caption else ""
    return f"[{content_type}: {url}]{suffix}"


@dataclass(frozen=True)
class TelegramContext:
    """Platform context for Telegram messages.

    Unlike DiscordContext where message_id is always present, Telegram
    service messages (e.g. user joined, pinned message) have no message_id,
    so the field is optional. All ordinary bot-interaction messages will
    have a non-None message_id.
    """

    chat_id: int
    topic_id: int | None = None
    is_group: bool = False
    message_id: int | None = None


@dataclass(frozen=True)
class DiscordContext:
    guild_id: int | None
    channel_id: int
    message_id: int
    thread_id: int | None = None
    channel_type: Literal["text", "thread", "forum", "voice"] = "text"


PlatformContext = TelegramContext | DiscordContext


@dataclass
class Message:
    id: str
    platform: Platform
    bot_id: str
    user_id: str = field(repr=False)
    user_name: str = field(repr=False)
    is_mention: bool
    is_from_bot: bool
    content: MessageContent | str
    type: MessageType
    timestamp: datetime
    platform_context: PlatformContext
    trust_level: TrustLevel  # required — caller must provide resolved trust level
    # Security: adapters must always set trust="user". Only internal hub code
    # may set trust="system". Never derive trust from inbound channel data.
    trust: Literal["user", "system"] = "user"
    metadata: dict[str, Any] = field(default_factory=dict)

    def extract_scope_id(self) -> str:
        """Extract conversation scope ID from platform context.

        Maps platform-specific context to a canonical scope string used
        for pool routing. See spec §Scope extraction rules.
        """
        ctx = self.platform_context
        if isinstance(ctx, TelegramContext):
            if ctx.topic_id is not None:
                return f"chat:{ctx.chat_id}:topic:{ctx.topic_id}"
            return f"chat:{ctx.chat_id}"
        if isinstance(ctx, DiscordContext):
            if ctx.thread_id is not None:
                return f"thread:{ctx.thread_id}"
            return f"channel:{ctx.channel_id}"
        raise ValueError(f"Unknown platform context type: {type(ctx)}")

    @classmethod
    def from_adapter(
        cls,
        *,
        platform: Platform,
        bot_id: str,
        user_id: str,
        user_name: str,
        content: MessageContent,
        type: MessageType,
        timestamp: datetime,
        trust_level: TrustLevel,
        is_mention: bool = False,
        is_from_bot: bool = False,
        platform_context: PlatformContext,
    ) -> "Message":
        """Construct a Message from an adapter.

        trust is always 'user' — never caller-controlled.
        trust_level must be provided by the adapter after resolving via AuthMiddleware.
        """
        return cls(
            id=f"{platform.value}:{user_id}:{int(timestamp.timestamp())}",
            platform=platform,
            bot_id=bot_id,
            user_id=user_id,
            user_name=user_name,
            is_mention=is_mention,
            is_from_bot=is_from_bot,
            content=content,
            type=type,
            timestamp=timestamp,
            platform_context=platform_context,
            trust_level=trust_level,
            trust="user",  # SECURITY: never caller-controlled
        )


@dataclass
class Response:
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
