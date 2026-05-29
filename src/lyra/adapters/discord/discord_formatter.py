"""DiscordFormatter — OutboundFormatter Protocol impl for Discord."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

import discord

from lyra.adapters.discord.discord_formatting import render_buttons, render_text
from lyra.adapters.shared._shared import DISCORD_MAX_LENGTH, send_with_retry
from lyra.core.messaging.render_events import (
    ReasoningDeltaRenderEvent,
    ReasoningEndRenderEvent,
    ReasoningStartRenderEvent,
)
from lyra.outbound._reasoning_accum import ReasoningAccumulator

if TYPE_CHECKING:
    from collections.abc import Callable

    from lyra.adapters.discord import DiscordAdapter
    from lyra.core.messaging.message import InboundMessage

log = logging.getLogger("lyra.adapters.discord")

# Channels that support get_partial_message(); _resolve_channel() returns one of these.
_PartialMessageable = (
    discord.TextChannel
    | discord.Thread
    | discord.DMChannel
    | discord.VoiceChannel
    | discord.StageChannel
)


class DiscordFormatter:
    """OutboundFormatter impl for Discord (DISCORD_MAX_LENGTH chunk, ui.View buttons).

    Encapsulates formatting, trace-rendering, and send-mechanics for Discord.
    """

    def __init__(  # noqa: PLR0913 — send-mechanics absorb added reply context args
        self,
        adapter: "DiscordAdapter",
        send_to_id: int,
        get_msg: "Callable[[str, str], str]",
        placeholder_text: str,
        reply_msg_id: int | None = None,
        should_reply: bool = False,
        original_msg: "InboundMessage | None" = None,
    ) -> None:
        self._adapter = adapter
        self._send_to_id = send_to_id
        self._get_msg = get_msg
        self._placeholder_text = placeholder_text
        self._reply_msg_id = reply_msg_id
        self._should_reply = should_reply
        self._original_msg = original_msg
        self._reasoning = ReasoningAccumulator()

    def placeholder_text(self) -> str:
        return self._placeholder_text

    def chunk(self, text: str) -> list[str]:
        return render_text(text, DISCORD_MAX_LENGTH)

    def render_text(self, text: str) -> list[str]:
        return render_text(text, DISCORD_MAX_LENGTH)

    def render_buttons(self, buttons: Any) -> discord.ui.View | None:
        return render_buttons(buttons)

    def dim_italic(self, text: str) -> str:
        return f"*{text}*"

    def get_msg(self, key: str, fallback: str) -> str:
        return self._get_msg(key, fallback)

    async def send_placeholder(self) -> tuple[Any, int]:
        messageable = await self._adapter._resolve_channel(self._send_to_id)
        if self._should_reply and self._reply_msg_id is not None:
            partial = cast(_PartialMessageable, messageable)
            msg_obj = partial.get_partial_message(self._reply_msg_id)
            placeholder = await msg_obj.reply(self._placeholder_text)
        else:
            placeholder = await messageable.send(self._placeholder_text)
        return placeholder, placeholder.id

    async def edit_placeholder_text(self, ph: Any, text: str) -> None:
        display = text[-DISCORD_MAX_LENGTH:]
        await send_with_retry(
            lambda d=display: ph.edit(content=d, embed=None),
            label="Intermediate text edit",
        )

    async def send_trace_placeholder(self) -> tuple[Any, int | None]:
        messageable = await self._adapter._resolve_channel(self._send_to_id)
        msg = await messageable.send("🔧 …")
        return msg, msg.id

    async def send_message(self, text: str) -> int | None:
        messageable = await self._adapter._resolve_channel(self._send_to_id)
        chunks = render_text(text, DISCORD_MAX_LENGTH)
        last_id = None
        for i, chunk in enumerate(chunks):
            is_last = i == len(chunks) - 1
            if is_last:
                try:
                    sent = await messageable.send(chunk)
                    last_id = sent.id
                except Exception as exc:  # noqa: BLE001 — terminal final-chunk send; type sanitized
                    log.warning(
                        "Failed to send final chunk to Discord: type=%s",
                        type(exc).__name__,
                    )
            else:
                await send_with_retry(
                    lambda c=chunk: messageable.send(c),
                    label="Final text chunk",
                )
        return last_id

    async def send_fallback(self, text: str) -> int | None:
        from lyra.adapters.discord.discord_outbound import send as _send
        from lyra.core.messaging.message import OutboundMessage

        fallback_outbound = (
            OutboundMessage.from_text(text)
            if text
            else OutboundMessage.from_text(self._placeholder_text)
        )
        if self._original_msg is not None:
            await _send(self._adapter, self._original_msg, fallback_outbound)
        return fallback_outbound.metadata.get("reply_message_id")

    async def edit_reasoning(
        self,
        trace_obj: Any,
        event: ReasoningStartRenderEvent
        | ReasoningDeltaRenderEvent
        | ReasoningEndRenderEvent,
    ) -> None:
        """Render reasoning events as italic text in the trace placeholder.

        Delta edits are throttled by STREAMING_EDIT_INTERVAL.
        Accumulated text is truncated to 120 chars with '…' suffix.
        The show_intermediate=False gate lives upstream on StreamProcessor — no
        adapter-side double-gate needed here.
        """
        if trace_obj is None:
            return
        text, should_edit = self._reasoning.process(event)
        if should_edit and text is not None:
            rendered = self.dim_italic(text)
            await send_with_retry(
                lambda t=rendered: trace_obj.edit(content=t, embed=None),
                label="Reasoning trace edit",
            )

    async def edit_tool_recap(
        self,
        trace_obj: Any,
        lines: list[str],
        done: bool,
    ) -> None:
        """Render tool recap card lines into the trace placeholder embed.

        Uses discord.Embed with green (done) or blue (in-progress) colour.
        """
        if not lines:
            return
        # Discord embed limits: title ≤256, description ≤4096 (per discord API).
        title = lines[0][:256]
        description = ("\n".join(lines[1:]) or "​")[
            :4096
        ]  # zero-width space placeholder
        color = discord.Color.green() if done else discord.Color.blue()
        embed = discord.Embed(title=title, description=description, color=color)
        try:
            await trace_obj.edit(embed=embed)
        except discord.DiscordException as exc:
            log.debug("Tool recap edit skipped: type=%s", type(exc).__name__)
