"""DiscordFormatter — OutboundFormatter Protocol impl for Discord."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

import discord

from factory.adapters.discord.discord_formatting import render_buttons, render_text
from factory.adapters.shared._shared import DISCORD_MAX_LENGTH, send_with_retry
from factory.core.messaging.render_events import (
    ReasoningDeltaRenderEvent,
    ReasoningEndRenderEvent,
    ReasoningStartRenderEvent,
)
from factory.outbound._reasoning_accum import ReasoningAccumulator
from factory.outbound.formatter import BaseFormatter

if TYPE_CHECKING:
    from collections.abc import Callable

    from factory.adapters.discord import DiscordAdapter
    from factory.core.messaging.message import InboundMessage

log = logging.getLogger("factory.adapters.discord")

# Channels that support get_partial_message(); _resolve_channel() returns one of these.
_PartialMessageable = (
    discord.TextChannel
    | discord.Thread
    | discord.DMChannel
    | discord.VoiceChannel
    | discord.StageChannel
)


class DiscordFormatter(BaseFormatter):
    """OutboundFormatter impl for Discord (DISCORD_MAX_LENGTH chunk, ui.View buttons).

    Inherits the BaseFormatter ABC contract; stores per-instance state directly
    (no super().__init__() — BaseFormatter is an ABC with no __init__).

    Discord-specific divergences:

    - ``send_placeholder``: reply-vs-thread logic (``should_reply`` guard +
      cast to ``_PartialMessageable``).
    - ``send_fallback``: delegates to ``discord_outbound.send`` so the full
      non-streaming send path (reply-to, metadata, typing) is reused.
    - **Single message**: tool recap + answer share one Discord message.
      ``send_trace_placeholder`` reuses the answer placeholder; recap sits in
      an embed (title on top), answer in the embed description below it.
      Overflow answer chunks still go as follow-up messages via ``send_message``.
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
        # Single-message composition state (recap + answer on one bubble).
        self._placeholder_msg: Any | None = None
        self._answer_text: str = ""
        self._recap_lines: list[str] = []
        self._recap_done: bool = False
        self._reasoning_display: str | None = None

    # ── Pure-formatting axis implementations ──────────────────────────────────

    def placeholder_text(self) -> str:
        return self._placeholder_text

    def dim_italic(self, text: str) -> str:
        return f"*{text}*"

    def get_msg(self, key: str, fallback: str) -> str:
        return self._get_msg(key, fallback)

    def chunk(self, text: str) -> list[str]:
        return render_text(text, DISCORD_MAX_LENGTH)

    def render_text(self, text: str) -> list[str]:
        return render_text(text, DISCORD_MAX_LENGTH)

    def render_buttons(self, buttons: Any) -> discord.ui.View | None:
        return render_buttons(buttons)

    # ── Platform-I/O axis — Discord-specific overrides ────────────────────────

    async def send_placeholder(self) -> tuple[Any, int]:
        """Send the placeholder message, replying to the original if appropriate.

        Reply-vs-thread: skip reply in threads (thread context makes it redundant).
        Cast to _PartialMessageable is required for get_partial_message().
        Cached for ``send_trace_placeholder`` reuse (single-message recap).
        """
        messageable = await self._adapter._resolve_channel(self._send_to_id)
        if self._should_reply and self._reply_msg_id is not None:
            partial = cast(_PartialMessageable, messageable)
            msg_obj = partial.get_partial_message(self._reply_msg_id)
            placeholder = await msg_obj.reply(self._placeholder_text)
        else:
            placeholder = await messageable.send(self._placeholder_text)
        self._placeholder_msg = placeholder
        self._answer_text = self._placeholder_text
        return placeholder, placeholder.id

    async def edit_placeholder_text(
        self, ph: Any, text: str, *, finalize: bool = False
    ) -> None:
        del finalize
        self._answer_text = text
        await self._render_combined(ph)

    async def send_trace_placeholder(self) -> tuple[Any, int | None]:
        """Reuse the answer placeholder — one Discord message for recap + reply.

        Emitter always sends the answer placeholder first; tools arrive later.
        Returning the same message object makes ``edit_tool_recap`` and
        ``edit_placeholder_text`` compose into a single bubble.
        """
        if self._placeholder_msg is not None:
            return self._placeholder_msg, self._placeholder_msg.id
        # Fallback (should not happen in normal streaming order).
        messageable = await self._adapter._resolve_channel(self._send_to_id)
        msg = await messageable.send(self._placeholder_text)
        self._placeholder_msg = msg
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
                except Exception as exc:  # noqa: BLE001 — DEBT:boundary-broad-catch# boundary: discord-send — terminal final-chunk; type sanitized
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
        """Fallback send — delegates to discord_outbound.send.

        Reuses the reply-to, metadata, and typing-indicator logic that lives in
        send() rather than duplicating it here.
        """
        from factory.adapters.discord.discord_outbound import send as _send
        from factory.core.messaging.message import OutboundMessage

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
        """Fold reasoning into the single combined message (dim italic).

        trace_obj=None means placeholder send failed — bail silently.
        Delta edits are throttled by ReasoningAccumulator.
        """
        if trace_obj is None:
            return
        text, should_edit = self._reasoning.process(event)
        if should_edit and text is not None:
            self._reasoning_display = self.dim_italic(text)
            await self._render_combined(trace_obj)

    async def edit_tool_recap(
        self,
        trace_obj: Any,
        lines: list[str],
        done: bool,
    ) -> None:
        """Render tool recap at the top of the single combined message.

        Recap is the embed title + leading description lines (green when done,
        blue while working). Answer text (if any) follows in the same embed.
        """
        if not lines:
            return
        self._recap_lines = lines
        self._recap_done = done
        await self._render_combined(trace_obj)

    def _compose_no_recap_display(self) -> str:
        """Plain-content body when no tool recap is active (head-sliced)."""
        answer = self._answer_text
        show_answer = bool(answer) and answer != self._placeholder_text
        body_parts: list[str] = []
        if self._reasoning_display:
            body_parts.append(self._reasoning_display)
        if show_answer:
            body_parts.append(answer)
        joined = "\n\n".join(body_parts) if body_parts else self._placeholder_text
        return joined[:DISCORD_MAX_LENGTH]

    def _compose_recap_embed(self) -> discord.Embed:
        """Build recap-on-top embed; answer reserved first in description budget."""
        _embed_desc_max = 4096
        sep = "\n\n"
        answer = self._answer_text
        show_answer = bool(answer) and answer != self._placeholder_text
        title = self._recap_lines[0][:256]
        recap_body = "\n".join(self._recap_lines[1:])
        reasoning = self._reasoning_display or ""
        answer_part = answer if show_answer else ""

        budget = _embed_desc_max
        if answer_part:
            budget -= len(answer_part)
            if recap_body or reasoning:
                budget -= len(sep)
        if recap_body and reasoning:
            budget -= len(sep)
            r_budget = min(len(reasoning), max(0, budget // 4))
            c_budget = max(0, budget - r_budget)
            recap_body = recap_body[:c_budget]
            reasoning = reasoning[:r_budget]
        elif recap_body:
            recap_body = recap_body[: max(0, budget)]
        elif reasoning:
            reasoning = reasoning[: max(0, budget)]

        desc_parts: list[str] = []
        if recap_body:
            desc_parts.append(recap_body)
        if reasoning:
            desc_parts.append(reasoning)
        if answer_part:
            desc_parts.append(answer_part)
        # Zero-width space keeps an empty embed description valid mid-flight.
        description = (sep.join(desc_parts) or "​")[:_embed_desc_max]
        color = discord.Color.green() if self._recap_done else discord.Color.blue()
        return discord.Embed(title=title, description=description, color=color)

    async def _render_combined(self, msg: Any) -> None:
        """Edit *msg* with recap (top) + optional reasoning + answer (bottom).

        Layout when tools ran:
          embed.title = recap header (🔧 Working… / Done ✅)
          embed.description = tool lines, then reasoning, then answer
          content cleared so the bubble is one visual card (recap first).

        Layout without tools: plain content = answer (or reasoning).

        Budget: embed description ≤4096. Answer is reserved first (emitter already
        head-chunks at DISCORD_MAX_LENGTH); recap/reasoning shrink if needed so
        the priced answer is never silently mid-cut by a fat recap.
        """
        if not self._recap_lines:
            display = self._compose_no_recap_display()
            await send_with_retry(
                lambda d=display: msg.edit(content=d, embed=None),
                label="Combined text edit",
            )
            return

        embed = self._compose_recap_embed()
        # content="" drops the old "…" so only the embed card shows.
        await send_with_retry(
            lambda e=embed: msg.edit(content="", embed=e),
            label="Combined recap edit",
        )
