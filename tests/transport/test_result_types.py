"""Unit tests for lyra.transport._result boundary types (SC-07, U1, U5)."""

from __future__ import annotations

import dataclasses
from typing import AsyncIterator

import pytest

from lyra.transport._result import Err, InboxStream, Ok, Result, SanitizedError


def test_ok_isinstance() -> None:
    x: Result[int, str] = Ok(42)
    assert isinstance(x, Ok)
    assert x.value == 42


def test_err_isinstance() -> None:
    x: Result[int, str] = Err("boom")
    assert isinstance(x, Err)
    assert x.error == "boom"


def test_ok_frozen() -> None:
    x: Ok[int] = Ok(99)
    with pytest.raises((dataclasses.FrozenInstanceError, TypeError)):
        x.value = 0  # type: ignore[misc]


def test_err_frozen() -> None:
    x: Err[str] = Err("oops")
    with pytest.raises((dataclasses.FrozenInstanceError, TypeError)):
        x.error = "changed"  # type: ignore[misc]


def test_err_narrowing_returns_str() -> None:
    def extract_error(result: Result[int, str]) -> str:
        assert isinstance(result, Err)
        return result.error  # pyright narrows to str here

    value = extract_error(Err("network failure"))
    assert isinstance(value, str)
    assert value == "network failure"


def test_sanitized_error_frozen() -> None:
    se = SanitizedError(
        code="transport.timeout", message="TimeoutError", retryable=True
    )
    assert se.retryable is True
    with pytest.raises((dataclasses.FrozenInstanceError, TypeError)):
        se.retryable = False  # type: ignore[misc]


def test_sanitized_error_detail_defaults_none() -> None:
    se = SanitizedError(code="transport.err", message="ValueError", retryable=False)
    assert se.detail is None


def test_sanitized_error_detail_explicit() -> None:
    se = SanitizedError(
        code="transport.err", message="ValueError", retryable=False, detail="extra"
    )
    assert se.detail == "extra"


async def _stub_messages() -> AsyncIterator[Result[bytes, SanitizedError]]:
    yield Ok(b"")


def test_inbox_stream_constructs() -> None:
    stream: InboxStream = InboxStream(
        inbox_subject="_INBOX.test.123",
        messages=_stub_messages(),
    )
    assert stream.inbox_subject == "_INBOX.test.123"
    assert stream.messages is not None


def test_inbox_stream_frozen() -> None:
    stream = InboxStream(inbox_subject="_INBOX.x", messages=_stub_messages())
    with pytest.raises((dataclasses.FrozenInstanceError, TypeError)):
        stream.inbox_subject = "changed"  # type: ignore[misc]
