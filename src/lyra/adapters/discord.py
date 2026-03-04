from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

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

DISCORD_MAX_LENGTH = 2000  # Discord API message length limit


@dataclass(frozen=True)
class DiscordConfig:
    token: str = field(repr=False)


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
        super().__init__(intents=intents)
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
        if not self.intents.message_content:
            log.warning(
                "message_content intent is disabled — "
                "guild message content will be empty. "
                "Enable 'Message Content Intent' in the Discord Developer Portal."
            )

    def _normalize(self, message: Any) -> Message:
        """Convert a discord.py Message (or SimpleNamespace) to a hub Message.

        Security: trust is always 'user' via Message.from_adapter().
        Never logs the bot token.
        """
        is_mention = self._bot_user is not None and self._bot_user in message.mentions

        # Strip @mention prefix so content reaches the agent clean
        content = message.content
        if is_mention and self._bot_user:
            content = re.sub(
                rf"<@!?{self._bot_user.id}>",
                "",
                content,
            ).strip()

        # Detect channel type
        channel_type: str = "text"
        if isinstance(message.channel, discord.Thread):
            channel_type = "thread"
        elif isinstance(message.channel, discord.ForumChannel):
            channel_type = "forum"
        elif isinstance(message.channel, discord.VoiceChannel):
            channel_type = "voice"

        ctx = DiscordContext(
            guild_id=message.guild.id if message.guild else None,
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

        _display_name = getattr(message.author, "display_name", None)
        hub_msg = Message.from_adapter(
            platform=Platform.DISCORD,
            bot_id=self._bot_id,
            user_id=f"dc:user:{message.author.id}",
            user_name=(
                _display_name if _display_name is not None else message.author.name
            ),
            content=TextContent(text=content),
            type=MessageType.TEXT,
            timestamp=message.created_at,
            is_mention=is_mention,
            is_from_bot=message.author.bot,
            platform_context=ctx,
        )
        return hub_msg

    async def on_message(self, message: Any) -> None:
        """Handle incoming Gateway message.

        Filters own/bot messages, applies backpressure, and enqueues to hub bus.
        """
        # S3: discard bot's own messages; fallback to message.author.bot pre-on_ready
        if message.author == self._bot_user or (
            self._bot_user is None and message.author.bot
        ):
            return

        try:
            hub_msg = self._normalize(message)
        except Exception:
            log.exception("Failed to normalize discord message id=%s", message.id)
            return

        # Discard messages from other bots (third-party bot filter)
        if hub_msg.is_from_bot:
            return

        # S5: backpressure — send ack before blocking on full bus
        if self._hub.bus.full():
            await message.reply("Processing your request\u2026")

        # S6: push to bus
        await self._hub.bus.put(hub_msg)

    async def send(self, original_msg: Message, response: Response) -> None:
        """Send response back to Discord.

        Fetches channel from cache (or network fallback) to avoid storing raw
        discord.py objects in hub domain metadata.
        Uses message.reply() for @-mentions, channel.send() otherwise.
        Content is truncated to Discord's 2000-char limit.
        """
        if not isinstance(original_msg.platform_context, DiscordContext):
            log.error(
                "send() called with non-DiscordContext for msg_id=%s", original_msg.id
            )
            return

        ctx = original_msg.platform_context
        channel = self.get_channel(ctx.channel_id)
        if channel is None:
            channel = await self.fetch_channel(ctx.channel_id)

        content = response.content[:DISCORD_MAX_LENGTH]

        messageable = cast(discord.abc.Messageable, channel)
        if original_msg.is_mention:
            msg = await messageable.fetch_message(ctx.message_id)
            await msg.reply(content)
        else:
            await messageable.send(content)
