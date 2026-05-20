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


@dataclass(frozen=True)
class SanitizedError:
    """message = type(exc).__name__ only — never str(exc)."""

    code: str
    message: str
    retryable: bool
    detail: str | None = None


@dataclass(frozen=True)
class InboxStream:
    """Typed return for transport.open_inbox() context manager (B1 consensus)."""

    inbox_subject: str
    messages: AsyncIterator[Result[bytes, SanitizedError]]
