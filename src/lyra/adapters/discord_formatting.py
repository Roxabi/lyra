"""Text formatting and UI helpers for DiscordAdapter."""

from __future__ import annotations

import logging
import re
from typing import Any

import discord
from tabulate import tabulate

from lyra.adapters._shared import AUDIO_MIME_TYPES, chunk_text
from lyra.core.messaging.message import Attachment, InboundMessage, Platform

log = logging.getLogger("lyra.adapters.discord")

_TABLE_RE = re.compile(
    r"(?m)"
    r"(?:^\|.+\|\s*\n)"  # header row
    r"(?:^\|[\s\-:|]+\|\s*\n)"  # separator row  (--|:--:|--:  etc.)
    r"(?:^\|.+\|[ \t]*\n?)+",  # one or more data rows
)


def _parse_md_table(match: re.Match[str]) -> str:
    """Convert a Markdown pipe table match to a tabulate ``simple`` code block."""
    lines = [ln for ln in match.group(0).splitlines() if ln.strip()]
    if len(lines) < 3:  # noqa: PLR2004 — need header + sep + ≥1 data row
        return match.group(0)

    def _row(line: str) -> list[str]:
        parts = line.split("|")
        if parts and parts[0].strip() == "":
            parts = parts[1:]
        if parts and parts[-1].strip() == "":
            parts = parts[:-1]
        return [c.strip() for c in parts]

    headers = _row(lines[0])
    # lines[1] is the separator row — skip it
    data = [_row(ln) for ln in lines[2:]]
    return f"```\n{tabulate(data, headers=headers, tablefmt='simple')}\n```"


_MENTION_RE = re.compile(r"<@!?\d+>")
_THREAD_SPLIT_RE = re.compile(r"\s*(?:—|--)\s*")


def make_thread_name(content: str, fallback: str) -> str:
    """Derive a Discord thread name from a message.

    Takes the segment before the first ' — ' (em-dash) or '--' (double dash),
    strips @mentions, collapses whitespace, and falls back to *fallback* when
    the result is empty. The return value is always ≤ 100 characters (Discord limit).
    """
    segment = _THREAD_SPLIT_RE.split(content, maxsplit=1)[0]
    clean = _MENTION_RE.sub("", segment).strip()
    name = " ".join(clean.split()) or fallback
    return name[:100]


def extract_attachments(raw_attachments: list[Any]) -> list[Attachment]:
    """Extract non-audio Attachment objects from Discord message.attachments."""
    result: list[Attachment] = []
    for a in raw_attachments:
        ct = getattr(a, "content_type", None) or ""
        if ct in AUDIO_MIME_TYPES:
            continue
        if ct.startswith("image/"):
            att_type = "image"
        elif ct.startswith("video/"):
            att_type = "video"
        else:
            att_type = "file"
        result.append(
            Attachment(
                type=att_type,
                url_or_path_or_bytes=a.url,
                mime_type=ct or "application/octet-stream",
                filename=getattr(a, "filename", None),
            )
        )
    return result


def render_text(text: str, max_length: int = 2000) -> list[str]:
    """Split text into ≤max_length-char chunks.

    Markdown pipe tables are converted to tabulate code blocks first,
    since Discord does not render pipe-syntax tables.
    """
    text = _TABLE_RE.sub(_parse_md_table, text)
    return chunk_text(text, max_length)


def render_buttons(buttons: list[Any]) -> discord.ui.View | None:
    """Convert list[Button] to discord.ui.View, or None if empty."""
    if not buttons:
        return None
    view = discord.ui.View()
    for b in buttons:
        view.add_item(discord.ui.Button(label=b.text, custom_id=b.callback_data))
    return view


def _validate_inbound(
    inbound: InboundMessage, caller: str
) -> tuple[int, int | None, int | None] | None:
    """Validate platform is Discord and extract (channel_id, thread_id, message_id).

    Returns None on validation failure (logs error).
    Mirrors telegram_formatting._validate_inbound().
    """
    if inbound.platform != Platform.DISCORD.value:
        log.error("%s called with non-discord message id=%s", caller, inbound.id)
        return None
    channel_id: int | None = inbound.platform_meta.get("channel_id")
    if channel_id is None:
        log.error(
            "%s: platform_meta missing 'channel_id' for msg id=%s", caller, inbound.id
        )
        return None
    thread_id: int | None = inbound.platform_meta.get("thread_id")
    message_id: int | None = inbound.platform_meta.get("message_id")
    return channel_id, thread_id, message_id
