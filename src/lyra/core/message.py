from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel


class Platform(str, Enum):
    TELEGRAM = "telegram"
    DISCORD = "discord"


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


MessageContent = TextContent | ImageContent | AudioContent


@dataclass(frozen=True)
class TelegramContext:
    chat_id: int
    topic_id: int | None = None
    is_group: bool = False


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
    # Security: adapters must always set trust="user". Only internal hub code
    # may set trust="system". Never derive trust from inbound channel data.
    trust: Literal["user", "system"] = "user"
    metadata: dict[str, Any] = field(default_factory=dict)

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
        is_mention: bool = False,
        is_from_bot: bool = False,
        platform_context: PlatformContext,
    ) -> "Message":
        """Construct a Message from an adapter.

        trust is always 'user' — never caller-controlled.
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
            trust="user",  # SECURITY: never caller-controlled
        )


@dataclass
class Response:
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
