"""Tests for WorkScope NATS subject-injection allowlist (#1393).

The platform/bot_id fields flow into `lyra.typing.{platform}.{bot_id}` (and other
future subjects); NATS special chars (`.`, `*`, `>`) MUST be rejected at the
dataclass boundary so callers can never publish to a wildcard-matching subject.
"""

from __future__ import annotations

import pytest

from lyra.transport.work_scope import WorkScope


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
    """Values matching ^[A-Za-z0-9_-]+$ must construct cleanly."""

    @pytest.mark.parametrize(
        "value",
        ["telegram", "discord", "x", "bot-1", "bot_2", "ABC", "0", "a-b_c-9"],
    )
    def test_platform_allowed(self, value: str) -> None:
        assert _make(platform=value).platform == value

    @pytest.mark.parametrize(
        "value",
        ["x", "bot-1", "bot_2", "AlphaBot", "123", "a-b_c-9"],
    )
    def test_bot_id_allowed(self, value: str) -> None:
        assert _make(bot_id=value).bot_id == value


class TestRejected:
    """NATS special chars and other unsafe inputs MUST raise at construction."""

    @pytest.mark.parametrize(
        "value",
        [
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
        ],
    )
    def test_platform_rejected(self, value: str) -> None:
        with pytest.raises(ValueError, match="platform"):
            _make(platform=value)

    @pytest.mark.parametrize(
        "value",
        ["x.y", "*", ">", "x.>", "a b", "", "a/b", "a\nb"],
    )
    def test_bot_id_rejected(self, value: str) -> None:
        with pytest.raises(ValueError, match="bot_id"):
            _make(bot_id=value)

    def test_error_message_includes_offending_value(self) -> None:
        """The ValueError MUST cite the offending value to aid debugging."""
        with pytest.raises(ValueError, match=r"x\.>"):
            _make(bot_id="x.>")
