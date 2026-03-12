from __future__ import annotations

from datetime import datetime, timezone

from lyra.core.auth import TrustLevel
from lyra.core.message import (
    Message,
    MessageType,
    Platform,
    TelegramContext,
    TextContent,
)


class CLIAdapter:
    """Minimal inbound-only CLI adapter. Always OWNER trust (local use only).

    NOT a ChannelAdapter — does not implement send()/send_streaming().
    Not registered with hub.register_adapter() in this issue.
    """

    def __init__(self, bot_id: str = "cli") -> None:
        self._bot_id = bot_id

    def on_input(self, text: str, user_id: str = "cli:user:local") -> Message:
        """Convert a CLI text input to a Message with TrustLevel.OWNER."""
        return Message.from_adapter(
            platform=Platform.CLI,
            bot_id=self._bot_id,
            user_id=user_id,
            user_name="local",
            content=TextContent(text=text),
            type=MessageType.TEXT,
            timestamp=datetime.now(timezone.utc),
            trust_level=TrustLevel.OWNER,
            # placeholder — no CLIContext type exists yet
            platform_context=TelegramContext(chat_id=0),
        )
