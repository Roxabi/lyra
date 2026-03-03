from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Literal

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
    guild_id: int
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
    channel: str  # deprecated alias for platform.value — kept until migration complete
    user_id: str
    user_name: str
    is_mention: bool
    is_from_bot: bool
    content: MessageContent | str
    type: MessageType
    timestamp: datetime
    platform_context: PlatformContext
    # Security: adapters must always set trust="user". Only internal hub code
    # may set trust="system". Never derive trust from inbound channel data.
    trust: Literal["user", "system"] = "user"
    metadata: dict = field(default_factory=dict)


@dataclass
class Response:
    content: str
    metadata: dict = field(default_factory=dict)
