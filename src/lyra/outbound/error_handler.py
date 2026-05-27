"""OutboundErrorHandler — single broad-catch site for outbound boundaries.

Wraps platform-side exceptions into SanitizedError (type(exc).__name__ only,
never str(exc)) and routes them via Result[T, SanitizedError]. Replaces the
13 ad-hoc `except Exception:  # noqa: BLE001` sites that used to live in
OutboundEmitter (Phase 2 of stage-axis refactor, #1279).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, TypeVar

from lyra.transport._result import Err, Ok, Result, SanitizedError

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

T = TypeVar("T")

# Stream-error fallback messages (moved from _shared_streaming_state.py).
_ERR_TIMEOUT_FALLBACK = (
    "⏱️ The backend took longer than 120 s to respond. Please try again."
)
_ERR_NO_FINAL_FALLBACK = (
    "⚠️ Response ended without a final message (tool events only). Please try again."
)


class OutboundErrorHandler:
    """Composable error-handling stage for outbound emitters."""

    def __init__(self, get_msg: Callable[[str, str], str]) -> None:
        self._get_msg = get_msg

    def get_msg(self, key: str, fallback: str) -> str:
        return self._get_msg(key, fallback)

    def handle(self, exc: BaseException, *, context: str) -> SanitizedError:
        """Sanitize an exception to a Result-payload-safe error.

        message is `type(exc).__name__` only — never `str(exc)`. This is the
        single discipline that keeps outbound bus payloads free of hostnames,
        connection strings, auth-token fragments, and other exception-string
        contents from httpx / aiohttp / nats / aiogram / discord.
        """
        return SanitizedError(
            code=context,
            message=type(exc).__name__,
            retryable=False,
        )

    async def guard(
        self,
        call: Callable[[], Awaitable[T]],
        *,
        context: str,
    ) -> Result[T, SanitizedError]:
        """Wrap an async callable; return Ok(value) on success, Err on exception.

        Callable form (NOT coroutine form) to mirror the
        `send_with_retry(lambda: ..., label=...)` precedent in
        `lyra.adapters.shared._shared`. Avoids un-awaited-coroutine warnings
        if guard short-circuits.
        """
        try:
            value = await call()
            return Ok(value)
        except Exception as exc:  # noqa: BLE001 — single catch site for outbound boundaries
            log.debug("outbound boundary %s: %s", context, type(exc).__name__)
            return Err(self.handle(exc, context=context))

    def classify_stream_error(
        self,
        stream_error: Exception | None,
        *,
        had_tool_events: bool,
        final_text: str | None,
    ) -> str | None:
        """Render a user-facing string for terminal error states.

        Moved from lyra.adapters.shared._shared_streaming_state.classify_stream_error.
        Signature loses the msg_fn arg — uses self._get_msg instead. Same semantics:
        returns None when there's no error and a final text is present (caller renders
        final_text normally); returns a descriptive string for every error branch
        so callers never fall through to a bare GENERIC_ERROR_REPLY silently.

        Uses `type(stream_error).__name__` only — never `str(stream_error)`.
        """
        from lyra.core.exceptions import StreamChunkTimeout

        if stream_error is not None:
            if isinstance(stream_error, StreamChunkTimeout):
                return self._get_msg("error_timeout", _ERR_TIMEOUT_FALLBACK)
            return self._get_msg(
                "error_stream",
                f"⚠️ Streaming error: {type(stream_error).__name__}. Please try again.",
            )
        if final_text is None and had_tool_events:
            return self._get_msg("error_no_final", _ERR_NO_FINAL_FALLBACK)
        return None
