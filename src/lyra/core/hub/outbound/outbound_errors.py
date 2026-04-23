"""Outbound dispatcher helpers — error classification, constants, notifications.

Extracted from outbound_dispatcher.py to keep each module ≤300 LOC.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ...messaging.message import InboundMessage

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SEND_ERROR_MSG = "⚠️ I encountered an error sending my response. Please try again."
_CIRCUIT_OPEN_MSG = "⚠️ I'm temporarily unavailable. Please try again in a moment."
_CIRCUIT_NOTIFY_DEBOUNCE = 60.0  # seconds between circuit-open notifications per chat
_SCOPE_REAP_THRESHOLD = 256  # reap idle scope locks when dict exceeds this size
_NOTIFY_TS_REAP_THRESHOLD = 512  # reap stale circuit-notify timestamps when > this size

# Queue item shapes (heterogeneous tuple — see OutboundDispatcher._dispatch_item):
#   ("send",         InboundMessage, OutboundMessage)
#   ("streaming",    InboundMessage, AsyncIterator[RenderEvent], OutboundMessage | None)
#   ("audio",        InboundMessage, OutboundAudio)
#   ("audio_stream", InboundMessage, AsyncIterator[OutboundAudioChunk])
#   ("voice_stream", InboundMessage, AsyncIterator[OutboundAudioChunk])
#   ("attachment",   InboundMessage, OutboundAttachment)
_ITEM = tuple


# ---------------------------------------------------------------------------
# Transient-error classifier
# ---------------------------------------------------------------------------


def _is_transient_error(exc: BaseException) -> bool:
    """Return True if *exc* is a transient network/server error worth retrying.

    Identifies transient errors by exception class name and module to avoid
    hard imports of aiogram or discord.py at import time (those packages may
    not be installed in all environments).

    Retryable: network errors, 5xx server errors, rate-limit (429).
    Not retryable: 4xx client errors (bad token, chat not found, forbidden).
    """
    cls = type(exc)
    module = cls.__module__ or ""
    name = cls.__name__

    # asyncio / stdlib transients
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
        return True

    # aiohttp transients (used by both aiogram and discord.py internally)
    if module == "aiohttp" or module.startswith("aiohttp."):
        return True

    # aiogram: TelegramNetworkError is transient; TelegramAPIError subclasses
    # with status >= 500 are transient; 4xx (bad token, chat not found) are not.
    if module == "aiogram" or module.startswith("aiogram."):
        if "NetworkError" in name or "ServerError" in name:
            return True
        # TelegramRetryAfter → rate limit → retryable
        if "RetryAfter" in name:
            return True
        # For generic TelegramAPIError, check status code attribute
        status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
        if status is not None and isinstance(status, int):
            return status == 429 or status >= 500
        return False

    # discord.py: HTTPException with status >= 500 or 429 is transient
    if module == "discord" or module.startswith("discord."):
        if "GatewayNotFound" in name or "ConnectionClosed" in name:
            return True
        status = getattr(exc, "status", None)
        if status is not None and isinstance(status, int):
            return status == 429 or status >= 500
        return False

    return False


# ---------------------------------------------------------------------------
# User notification helper
# ---------------------------------------------------------------------------


async def try_notify_user(
    platform_name: str,
    adapter: Any,
    msg: "InboundMessage",
    text: str,
    *,
    circuit: Any = None,  # CircuitBreaker | None — avoids hard import
) -> None:
    """Send a short plaintext notification directly, bypassing the queue.

    Attempts a bare platform send without retry. Any failure is logged and
    swallowed so the caller's error path is never disrupted.

    When *circuit* is provided and open, the notification is suppressed to
    avoid amplifying failures against an already-degraded platform.
    """
    if circuit is not None and getattr(circuit, "is_open", lambda: False)():
        log.debug(
            "OutboundDispatcher[%s]: suppressing user notification (circuit open)",
            platform_name,
        )
        return
    try:
        from ...messaging.message import OutboundMessage as _OM

        outbound = _OM(content=[text])
        await adapter.send(msg, outbound)
    except Exception as notify_exc:
        log.warning(
            "OutboundDispatcher[%s]: failed to send user notification: %s",
            platform_name,
            notify_exc,
        )
