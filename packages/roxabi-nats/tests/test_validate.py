"""Boundary tests for validate_nats_token.

Verifies the security-relevant input barrier used for NATS subject prefixes,
queue group names, and other identifiers interpolated into NATS protocol
lines. Covers the cases a fuzzer would try first: wildcards, spaces, empty
strings, and common protocol-injection vectors.
"""

from __future__ import annotations

import pytest

from roxabi_nats._validate import validate_nats_token


class TestValid:
    @pytest.mark.parametrize(
        "value",
        [
            "a",
            "Z",
            "0",
            "lyra.inbound.telegram.bot_1",
            "queue-group-alpha",
            "hub_primary",
            "v1.2.3",
            "a.b.c.d.e",
            "A-B_C.0",
        ],
    )
    def test_accepts_valid_identifiers(self, value: str) -> None:
        validate_nats_token(value, kind="subject")


class TestRejected:
    @pytest.mark.parametrize(
        "value",
        [
            "lyra.*",
            "lyra.>",
            ">",
            "*",
            "lyra.inbound.*.bot",
            "lyra inbound",
            " leading-space",
            "trailing-space ",
            "with\ttab",
            "with\nnewline",
            "slash/notallowed",
            "at@sign",
            "question?",
            "semi;colon",
            "hash#tag",
        ],
    )
    def test_rejects_invalid_identifiers(self, value: str) -> None:
        with pytest.raises(ValueError, match="subject"):
            validate_nats_token(value, kind="subject")


class TestEmpty:
    def test_empty_rejected_by_default(self) -> None:
        with pytest.raises(ValueError, match="queue_group"):
            validate_nats_token("", kind="queue_group")

    def test_empty_accepted_when_allow_empty(self) -> None:
        validate_nats_token("", kind="queue_group", allow_empty=True)

    def test_whitespace_only_rejected_even_with_allow_empty(self) -> None:
        with pytest.raises(ValueError):
            validate_nats_token("   ", kind="queue_group", allow_empty=True)


class TestErrorMessage:
    def test_message_includes_kind_and_value(self) -> None:
        with pytest.raises(ValueError) as exc_info:
            validate_nats_token("bad*token", kind="subject_prefix")
        msg = str(exc_info.value)
        assert "subject_prefix" in msg
        assert "bad*token" in msg
