"""DiscordFormatter — OutboundFormatter Protocol impl for Discord."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

import discord

from lyra.adapters.discord.discord_formatting import render_buttons, render_text
from lyra.adapters.shared._shared import DISCORD_MAX_LENGTH, send_with_retry
from lyra.core.messaging.render_events import (
    ReasoningDeltaRenderEvent,
    ReasoningEndRenderEvent,
    ReasoningStartRenderEvent,
)
from lyra.outbound.throttle import STREAMING_EDIT_INTERVAL

if TYPE_CHECKING:
    from collections.abc import Callable

    from lyra.adapters.discord import DiscordAdapter

log = logging.getLogger("lyra.adapters.discord")


class DiscordFormatter:
    """OutboundFormatter impl for Discord (DISCORD_MAX_LENGTH chunk, ui.View buttons).

    Encapsulates pure formatting and trace-rendering logic extracted from
    discord_outbound.build_streaming_callbacks closures (issue #1279, Slice 5).
    Send-mechanics (send_placeholder, edit_placeholder_text, send_message, etc.)
    remain in build_streaming_callbacks for the legacy transition path.
    """

    def __init__(
        self,
        adapter: "DiscordAdapter",
        send_to_id: int,
        get_msg: "Callable[[str, str], str]",
        placeholder_text: str,
    ) -> None:
        self._adapter = adapter
        self._send_to_id = send_to_id
        self._get_msg = get_msg
        self._placeholder_text = placeholder_text
        self._reasoning_accum: str = ""
        self._last_reasoning_edit: float | None = None

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

    async def edit_reasoning(  # noqa: C901 — three-branch state machine
        self,
        trace_obj: Any,
        event: ReasoningStartRenderEvent
        | ReasoningDeltaRenderEvent
        | ReasoningEndRenderEvent,
    ) -> None:
        """Render reasoning events as italic text in the trace placeholder.

        Extracted from discord_outbound.build_streaming_callbacks._render_reasoning.
        Delta edits are throttled by STREAMING_EDIT_INTERVAL.
        Accumulated text is truncated to 120 chars with '…' suffix.
        The show_intermediate=False gate lives upstream on StreamProcessor — no
        adapter-side double-gate needed here.
        """
        if isinstance(event, ReasoningStartRenderEvent):
            if trace_obj is None:
                return
            self._reasoning_accum = ""
            self._last_reasoning_edit = None

        elif isinstance(event, ReasoningDeltaRenderEvent):
            if trace_obj is None:
                return
            self._reasoning_accum += event.delta
            truncated = self._reasoning_accum
            if len(truncated) > 120:  # noqa: PLR2004
                truncated = truncated[:117] + "…"
            now = time.monotonic()
            if (
                self._last_reasoning_edit is None
                or (now - self._last_reasoning_edit) >= STREAMING_EDIT_INTERVAL
            ):
                text = self.dim_italic(truncated)
                await send_with_retry(
                    lambda t=text: trace_obj.edit(content=t, embed=None),
                    label="Reasoning trace edit",
                )
                self._last_reasoning_edit = now

        else:  # ReasoningEndRenderEvent
            if trace_obj is None:
                return
            if self._reasoning_accum:
                truncated = self._reasoning_accum
                if len(truncated) > 120:  # noqa: PLR2004
                    truncated = truncated[:117] + "…"
                text = self.dim_italic(truncated)
                await send_with_retry(
                    lambda t=text: trace_obj.edit(content=t, embed=None),
                    label="Reasoning trace edit",
                )

    async def edit_tool_recap(
        self,
        trace_obj: Any,
        lines: list[str],
        done: bool,
    ) -> None:
        """Render tool recap card lines into the trace placeholder embed.

        Extracted from discord_outbound.build_streaming_callbacks._edit_tool_recap.
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
