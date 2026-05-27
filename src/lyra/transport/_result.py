"""Boundary types for the NATS transport layer.

Result is exception-free: all layer boundaries return Ok[T] | Err[E].
SanitizedError.message carries type(exc).__name__ only — never str(exc).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Generic, TypeVar, Union

T = TypeVar("T")
E = TypeVar("E")


@dataclass(frozen=True)
class Ok(Generic[T]):
    value: T


@dataclass(frozen=True)
class Err(Generic[E]):
    error: E


Result = Union[Ok[T], Err[E]]


_BUS_MESSAGE_MAX_LEN = 200


@dataclass(frozen=True)
class SanitizedError:
    """message = type(exc).__name__ only — never str(exc)."""

    code: str
    message: str
    retryable: bool

    @classmethod
    def from_message(
        cls, message: str, *, code: str = "stream.error"
    ) -> "SanitizedError":
        """Scrub + bound arbitrary upstream wire text for safe bus propagation.

        Strips control characters and truncates to ``_BUS_MESSAGE_MAX_LEN``
        (mirrors ``_scrub_cli_error_text`` in cli_streaming_parser — same
        security boundary: untrusted upstream content must not reach the NATS
        bus raw). Falls back to ``"model_error"`` when ``message`` is empty.
        """
        if not message:
            return cls(code=code, message="model_error", retryable=False)
        scrubbed = "".join(c if c.isprintable() else " " for c in message)
        if len(scrubbed) > _BUS_MESSAGE_MAX_LEN:
            scrubbed = scrubbed[: _BUS_MESSAGE_MAX_LEN - 1] + "…"
        return cls(code=code, message=scrubbed or "model_error", retryable=False)


@dataclass(frozen=True)
class InboxStream:
    """Typed return for transport.open_inbox() context manager (B1 consensus)."""

    inbox_subject: str
    messages: AsyncIterator[Result[bytes, SanitizedError]]
