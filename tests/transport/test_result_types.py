"""Unit tests for lyra.transport._result boundary types (SC-07, U1, U5)."""

from __future__ import annotations

import dataclasses
from typing import AsyncIterator

import pytest

from lyra.transport import SanitizedError
from lyra.transport._result import Err, InboxStream, Ok, Result
from roxabi_contracts.errors import KNOWN_CODES


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


def test_sanitized_error_has_exactly_expected_fields() -> None:
    fields = {f.name for f in dataclasses.fields(SanitizedError)}
    assert fields == {"code", "message", "retryable"}


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


# ---------------------------------------------------------------------------
# SC-9 -- SanitizedError.from_message (Phase 5 #1282 addendum)
# ---------------------------------------------------------------------------


class TestSanitizedErrorFromMessage:
    """from_message scrubs + bounds arbitrary wire text (SC-9).

    Security contract: empty input falls back to "model_error"; control
    characters are replaced with a space; inputs longer than 200 chars are
    truncated to exactly 200 chars; clean short input is preserved verbatim.
    """

    def test_empty_input_returns_fallback(self) -> None:
        """Empty string falls back to message 'model_error'."""
        # Arrange / Act
        result = SanitizedError.from_message("")

        # Assert -- fallback applied
        assert result.message == "model_error"
        assert result.code == "stream.error"
        assert result.retryable is False

    def test_default_code_is_registered(self) -> None:
        """#1113: the default code flows to RunErrorRenderEvent.code, so the
        from_message default must be a registry-valid KNOWN_CODES key.

        Transport is intentionally roxabi_contracts-free at runtime, so the
        literal default has no import-time guard — this test is its guard.
        """
        default_code = SanitizedError.from_message("anything").code
        assert default_code in KNOWN_CODES
        assert default_code == "stream.error"

    def test_control_chars_are_stripped(self) -> None:
        """Non-printable control chars (NUL, BEL, newline, CR) become spaces."""
        # Arrange -- input containing NUL, BEL, newline, carriage-return
        raw = "before\x00after\x07newline\ncarriage\r"

        # Act
        result = SanitizedError.from_message(raw)

        # Assert -- no control characters in the scrubbed message
        assert all(c.isprintable() or c == " " for c in result.message)
        # Visible words are preserved
        assert "before" in result.message
        assert "after" in result.message
        assert "newline" in result.message
        assert "carriage" in result.message

    def test_long_input_truncated_to_200_chars(self) -> None:
        """Input longer than 200 chars is truncated to exactly 200 chars."""
        # Arrange -- 250 clean printable chars
        raw = "x" * 250

        # Act
        result = SanitizedError.from_message(raw)

        # Assert -- exactly 200 chars; ends with ellipsis sentinel
        assert len(result.message) == 200
        assert result.message.endswith("…")

    def test_clean_short_input_preserved_verbatim(self) -> None:
        """Clean input under 200 chars is preserved verbatim, unchanged."""
        # Arrange
        raw = "model_overloaded"

        # Act
        result = SanitizedError.from_message(raw)

        # Assert
        assert result.message == raw
        assert result.code == "stream.error"
        assert result.retryable is False

    def test_all_control_chars_becomes_whitespace_only(self) -> None:
        """All-control-char input scrubs to a whitespace-only message.

        Contract note: ``"".join(c if c.isprintable() else " " for c in raw)``
        substitutes each control char with a single space. The fallback
        ``scrubbed or "model_error"`` only fires when ``scrubbed`` is empty —
        whitespace-only strings are truthy, so the fallback is skipped. This
        test locks the current behavior so a future caller cannot silently
        rely on a "non-empty visible content" invariant that ``from_message``
        does not guarantee. (Security boundary unchanged: no exception text
        leaks; the result is just a whitespace-only banner.)
        """
        # Arrange -- 10 NUL bytes; all non-printable
        raw = "\x00" * 10

        # Act
        result = SanitizedError.from_message(raw)

        # Assert -- 10 spaces, NOT the "model_error" fallback
        assert result.message == " " * 10
        assert result.message != "model_error"
        assert result.code == "stream.error"
