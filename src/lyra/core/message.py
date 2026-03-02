from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class MessageType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    AUDIO = "audio"
    COMMAND = "command"
    SYSTEM = "system"


@dataclass
class Message:
    id: str
    channel: str         # "telegram" | "discord"
    user_id: str         # canonical ID (not the raw platform ID)
    content: str | dict  # text, image, audio…
    type: MessageType
    timestamp: datetime
    metadata: dict = field(default_factory=dict)
