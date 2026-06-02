"""Shared helpers for channel adapters."""

from __future__ import annotations

from factory.adapters.shared._base_outbound import OutboundAdapterBase
from factory.adapters.shared._shared import (
    ATTACHMENT_EXTS_BASE,
    DISCORD_MAX_LENGTH,
    TypingTaskManager,
    buffer_and_render_audio,
    parse_reply_to_id,
    push_to_hub_guarded,
    resolve_msg,
    send_with_retry,
)
from factory.adapters.shared._shared_text import (
    chunk_text,
    sanitize_filename,
    truncate_caption,
)
from factory.adapters.shared.cli import CLIAdapter
from factory.adapters.shared.outbound_listener import OutboundListener
from factory.outbound.emitter import OutboundEmitter as StreamingSession

__all__ = [
    "OutboundAdapterBase",
    "OutboundListener",
    "CLIAdapter",
    "StreamingSession",
    "TypingTaskManager",
    "ATTACHMENT_EXTS_BASE",
    "DISCORD_MAX_LENGTH",
    "buffer_and_render_audio",
    "chunk_text",
    "parse_reply_to_id",
    "push_to_hub_guarded",
    "resolve_msg",
    "sanitize_filename",
    "send_with_retry",
    "truncate_caption",
]
