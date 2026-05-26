"""Tests for WorkScope NATS subject-injection allowlist (#1393).

The platform/bot_id fields flow into `lyra.typing.{platform}.{bot_id}` (and other
future subjects); NATS special chars (`.`, `*`, `>`) MUST be rejected at the
dataclass boundary so callers can never publish to a wildcard-matching subject.
trace_id flows to wire payloads and logs and is bounded similarly (wider charset).
"""

from __future__ import annotations

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
            "",  # empty
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
