"""Shared helper for composing OutboundEmitter instances in platform adapters.

Extracts the common scaffolding from TelegramAdapter._make_emitter and
DiscordAdapter._make_emitter so each adapter only supplies the
platform-specific parts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from lyra.core.messaging.message import InboundMessage, OutboundMessage
    from lyra.outbound.emitter import OutboundEmitter


def _make_emitter(  # noqa: PLR0913 — arity: 4 platform-specific + 1 adapter + 2 msg/outbound
    adapter: Any,
    original_msg: "InboundMessage",
    outbound: "OutboundMessage | None",
    *,
    validate: Callable[["InboundMessage", str], tuple[Any, ...] | None],
    bad_msg: str,
    formatter_cls: type,
    formatter_kwargs_fn: Callable[[tuple[Any, ...]], dict[str, Any]],
    typing_cls: type,
    scope_id_fn: Callable[[tuple[Any, ...]], Any],
) -> "OutboundEmitter":
    """Compose an OutboundEmitter from shared pieces + platform-specific overrides."""
    from lyra.outbound.emitter import OutboundEmitter
    from lyra.outbound.error_handler import OutboundErrorHandler
    from lyra.outbound.formatter import BadFormatter

    meta = validate(original_msg, "_make_emitter")
    if meta is None:
        return OutboundEmitter(BadFormatter(bad_msg), outbound)

    placeholder_text = adapter._msg("stream_placeholder", "…")
    formatter = formatter_cls(
        adapter,
        get_msg=adapter._msg,
        placeholder_text=placeholder_text,
        **formatter_kwargs_fn(meta),
    )
    typing = typing_cls(adapter)
    handler = OutboundErrorHandler(get_msg=formatter.get_msg)
    return OutboundEmitter(
        formatter,
        outbound,
        error_handler=handler,
        typing=typing,
        typing_scope_id=scope_id_fn(meta),
    )
