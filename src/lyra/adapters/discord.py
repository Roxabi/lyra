from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import discord

if TYPE_CHECKING:
    from lyra.core.hub import Hub

from lyra.core.message import (
    DiscordContext,
    Message,
    MessageType,
    Platform,
    Response,
    TextContent,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiscordConfig:
    token: str


def load_discord_config() -> DiscordConfig:
    """Load Discord configuration from environment variables.

    Raises SystemExit if DISCORD_TOKEN is absent. Never logs the token.
    """
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit("Missing required env var: DISCORD_TOKEN")
    return DiscordConfig(token=token)


class DiscordAdapter(discord.Client):
    """Discord channel adapter — discord.py v2 Gateway mode.

    Security contract:
    - Never logs the bot token.
    - All inbound messages produce trust='user' via Message.from_adapter().
    - Bot's own messages are silently discarded.
    """

    def __init__(
        self,
        hub: "Hub",
        bot_id: str = "main",
        *,
        intents: discord.Intents | None = None,
    ) -> None:
        if intents is None:
            intents = discord.Intents.default()
            intents.message_content = True

        if isinstance(intents, discord.Intents):
            # Normal production path — initialise the full discord.Client machinery.
            super().__init__(intents=intents)
        else:
            # Test path: intents is a mock/stub. Skip discord.Client.__init__ so
            # ConnectionState does not reject the non-Intents object. Only the
            # minimal attributes consumed by our own methods are set here.
            pass

        self._hub = hub
        self._bot_id = bot_id
        # Set on on_ready; None until login completes. Tests set this directly.
        self._bot_user: Any = None

    async def on_ready(self) -> None:
        """Cache bot user on login. Required for mention detection in _normalize()."""
        self._bot_user = self.user
        log.info(
            "Discord bot ready: %s (id=%s)", self.user, getattr(self.user, "id", "?")
        )

    def _normalize(self, message: Any) -> Message:
        """Convert a discord.py Message (or SimpleNamespace) to a hub Message.

        Stores original message in metadata["discord_message"] for send().
        Security: trust is always 'user' via Message.from_adapter().
        Never logs the bot token.
        """
        is_mention = (
            self._bot_user is not None
            and self._bot_user in message.mentions
        )

        # Strip @mention prefix so content reaches the agent clean
        content = message.content
        if is_mention and self._bot_user:
            for prefix in (f"<@{self._bot_user.id}>", f"<@!{self._bot_user.id}>"):
                content = content.replace(prefix, "", 1)
            content = content.strip()

        # Detect channel type
        channel_type: str = "text"
        if isinstance(message.channel, discord.Thread):
            channel_type = "thread"
        elif hasattr(discord, "ForumChannel") and isinstance(
            message.channel, discord.ForumChannel
        ):
            channel_type = "forum"
        elif isinstance(message.channel, discord.VoiceChannel):
            channel_type = "voice"

        ctx = DiscordContext(
            guild_id=message.guild.id if message.guild else 0,
            channel_id=message.channel.id,
            message_id=message.id,
            thread_id=(
                message.channel.id
                if isinstance(message.channel, discord.Thread)
                else None
            ),
            channel_type=channel_type,
        )

        log.debug(
            "Normalizing discord message id=%s from user_id=dc:user:%s",
            message.id,
            message.author.id,
        )

        hub_msg = Message.from_adapter(
            platform=Platform.DISCORD,
            bot_id=self._bot_id,
            user_id=f"dc:user:{message.author.id}",
            user_name=(
                getattr(message.author, "display_name", None) or message.author.name
            ),
            content=TextContent(text=content),
            type=MessageType.TEXT,
            timestamp=message.created_at,
            is_mention=is_mention,
            is_from_bot=message.author.bot,
            platform_context=ctx,
        )
        hub_msg.metadata["discord_message"] = message
        return hub_msg

    async def _on_message(self, message: Any) -> None:
        """Handle incoming Gateway message: filter own, apply backpressure, enqueue.

        Internal method (mirrors TelegramAdapter._on_message pattern) for testability.
        """
        # S3: discard bot's own messages
        if message.author == self._bot_user:
            return

        hub_msg = self._normalize(message)

        # S5: backpressure — send ack before blocking on full bus
        if self._hub.bus.full():
            await message.reply("Processing your request\u2026")

        # S6: push to bus
        await self._hub.bus.put(hub_msg)

    async def on_message(self, message: Any) -> None:
        """discord.py Gateway event handler — delegates to _on_message."""
        await self._on_message(message)

    async def send(self, original_msg: Message, response: Response) -> None:
        """Send response back to Discord.

        Uses message.reply() for @-mentions (threaded reply), channel.send() otherwise.
        """
        assert isinstance(original_msg.platform_context, DiscordContext)
        discord_message = original_msg.metadata.get("discord_message")
        if discord_message is None:
            log.warning(
                "discord_message missing from metadata for msg_id=%s — cannot reply",
                original_msg.id,
            )
            return

        if original_msg.is_mention:
            await discord_message.reply(response.content)
        else:
            await discord_message.channel.send(response.content)
