from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel


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


@dataclass
class Message:
    id: str
    channel: str  # "telegram" | "discord"
    user_id: str  # canonical ID (not the raw platform ID)
    content: MessageContent | str  # str kept for backward compat during transition
    type: MessageType
    timestamp: datetime
    # Security: adapters must always set trust="user". Only internal hub code
    # may set trust="system". Never derive trust from inbound channel data.
    trust: Literal["user", "system"] = "user"  # adapters always set "user"
    metadata: dict = field(default_factory=dict)


@dataclass
class Response:
    content: str
    metadata: dict = field(default_factory=dict)
