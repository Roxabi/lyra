"""CLI channel adapter — local REPL/pipe input with OWNER trust.

This adapter is intentionally minimal: it converts raw text input into an
InboundMessage suitable for the hub bus. No network, no auth gate — the CLI
is always considered local/trusted.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from lyra.core.auth.trust import TrustLevel
from lyra.core.messaging.message import GenericMeta, InboundMessage


class CLIAdapter:
    """Minimal CLI adapter that wraps raw text in an InboundMessage.

    Trust level is always OWNER — CLI input is local only.
    """

    def on_input(self, text: str) -> InboundMessage:
        """Convert raw text input to an InboundMessage with OWNER trust.

        Args:
            text: Raw text from the CLI.

        Returns:
            InboundMessage ready to push onto the hub inbound bus.
        """
        now = datetime.now(timezone.utc)
        msg_id = f"cli:user:local:{int(now.timestamp())}:{uuid.uuid4().hex[:8]}"
        return InboundMessage(
            id=msg_id,
            platform="cli",
            bot_id="cli",
            scope_id="cli:local",
            user_id="cli:user:local",
            user_name="local",
            is_mention=False,
            text=text,
            text_raw=text,
            timestamp=now,
            trust_level=TrustLevel.OWNER,
            platform_meta=GenericMeta(),
        )
