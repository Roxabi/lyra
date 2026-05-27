"""Tests for WorkScope NATS subject-injection allowlist (#1393).

Supersedes the #1392 trace_id non-empty guard with a stricter allowlist:
platform/bot_id MUST match `^[A-Za-z0-9_-]{1,48}$`; trace_id MUST match
`^[A-Za-z0-9_-]{1,128}$`. The new pattern implies non-empty (length ≥ 1)
plus tight charset (rejects whitespace, control chars, NATS specials).
"""

from __future__ import annotations

import dataclasses

import pytest

from lyra.transport.work_scope import WorkScope

# Values shared by platform + bot_id (same allowlist + bound).
SUBJECT_FIELD_REJECTED = [
    "tele.gram",  # NATS token separator
    "*",  # NATS single-token wildcard
    ">",  # NATS multi-token wildcard
    "x.>",  # explicit wildcard injection (from issue body)
    "a b",  # space
    "",  # empty
    "a/b",  # path separator
    "héllo",  # non-ASCII
    "a\nb",  # newline
    "a\tb",  # tab
    "a;b",  # punctuation
    "a\x00b",  # null byte (log-truncation vector)
    "a\rb",  # carriage return (line-override vector)
    "a" * 49,  # over length cap (48)
]


def _make(**overrides: object) -> WorkScope:
    defaults: dict[str, object] = {
        "platform": "telegram",
        "bot_id": "x",
        "scope_id": 1,
        "trace_id": "t",
    }
    defaults.update(overrides)
    return WorkScope(**defaults)  # type: ignore[arg-type]


class TestAllowed:
    """Values matching the allowlist + length bound must construct cleanly."""

    @pytest.mark.parametrize(
        "value",
        ["telegram", "discord", "x", "bot-1", "bot_2", "ABC", "0", "a-b_c-9", "a" * 48],
    )
    def test_platform_allowed(self, value: str) -> None:
        assert _make(platform=value).platform == value

    @pytest.mark.parametrize(
        "value",
        ["x", "bot-1", "bot_2", "AlphaBot", "123", "a-b_c-9", "a" * 48],
    )
    def test_bot_id_allowed(self, value: str) -> None:
        assert _make(bot_id=value).bot_id == value

    @pytest.mark.parametrize(
        "value",
        ["t", "abc123", "550e8400-e29b-41d4-a716-446655440000", "a" * 128],
    )
    def test_trace_id_allowed(self, value: str) -> None:
        assert _make(trace_id=value).trace_id == value


class TestRejected:
    """NATS special chars, control chars, and over-length inputs MUST raise."""

    @pytest.mark.parametrize("value", SUBJECT_FIELD_REJECTED)
    def test_platform_rejected(self, value: str) -> None:
        with pytest.raises(ValueError, match="platform"):
            _make(platform=value)

    @pytest.mark.parametrize("value", SUBJECT_FIELD_REJECTED)
    def test_bot_id_rejected(self, value: str) -> None:
        with pytest.raises(ValueError, match="bot_id"):
            _make(bot_id=value)

    @pytest.mark.parametrize(
        "value",
        [
            "",  # empty (subsumes #1392 non-empty guard)
            "   ",  # whitespace-only (also subsumes #1392 strip-non-empty guard)
            "a b",  # space
            "a.b",  # dot — rejected for trace_id same as for platform/bot_id
            "a\nb",  # newline (log-injection)
            "a\rb",  # carriage return
            "a\x00b",  # null byte
            "a" * 129,  # over length cap (128)
        ],
    )
    def test_trace_id_rejected(self, value: str) -> None:
        with pytest.raises(ValueError, match="trace_id"):
            _make(trace_id=value)

    def test_error_message_includes_offending_value(self) -> None:
        """The ValueError MUST cite the offending value to aid debugging."""
        with pytest.raises(ValueError, match=r"x\.>"):
            _make(bot_id="x.>")


class TestDataclassReplace:
    """`dataclasses.replace` re-fires `__post_init__` on the new instance (#1392)."""

    def test_replace_rejects_empty_trace_id(self) -> None:
        scope = WorkScope(
            platform="telegram", bot_id="bot-1", scope_id=42, trace_id="abc123"
        )
        with pytest.raises(ValueError, match="trace_id"):
            dataclasses.replace(scope, trace_id="")

    def test_replace_rejects_invalid_platform(self) -> None:
        scope = WorkScope(
            platform="telegram", bot_id="bot-1", scope_id=42, trace_id="abc123"
        )
        with pytest.raises(ValueError, match="platform"):
            dataclasses.replace(scope, platform="bad.*")
