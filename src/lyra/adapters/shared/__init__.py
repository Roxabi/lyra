"""Shared helpers for channel adapters."""

from __future__ import annotations

from lyra.adapters.shared._base_outbound import OutboundAdapterBase
from lyra.adapters.shared._shared import (
    ATTACHMENT_EXTS_BASE,
    DISCORD_MAX_LENGTH,
    TypingTaskManager,
    buffer_and_render_audio,
    format_tool_summary_header,
    parse_reply_to_id,
    push_to_hub_guarded,
    resolve_msg,
    send_with_retry,
)
from lyra.adapters.shared._shared_streaming import PlatformCallbacks, StreamingSession
from lyra.adapters.shared._shared_text import (
    chunk_text,
    sanitize_filename,
    truncate_caption,
)
from lyra.adapters.shared.cli import CLIAdapter
from lyra.adapters.shared.outbound_listener import OutboundListener

__all__ = [
    "OutboundAdapterBase",
    "OutboundListener",
    "CLIAdapter",
    "PlatformCallbacks",
    "StreamingSession",
    "TypingTaskManager",
    "ATTACHMENT_EXTS_BASE",
    "DISCORD_MAX_LENGTH",
    "buffer_and_render_audio",
    "chunk_text",
    "format_tool_summary_header",
    "parse_reply_to_id",
    "push_to_hub_guarded",
    "resolve_msg",
    "sanitize_filename",
    "send_with_retry",
    "truncate_caption",
]
