"""Unit tests for the check_schema_version helper (issue #530).

Covers all rules described in _version_check.py:
- Missing field → v1 (legacy backwards compat)
- Exact match → accepted
- Receiver newer than payload → accepted (forward-compat)
- Receiver older than payload → dropped (mismatch)
- Non-int values (string, None, zero, negative) → dropped
- Counter isolation between independent callers
"""
from __future__ import annotations

import logging

import pytest

from lyra.nats._version_check import check_schema_version

# ---------------------------------------------------------------------------
# TestCheckSchemaVersion
# ---------------------------------------------------------------------------


class TestCheckSchemaVersion:
    """Unit tests for check_schema_version()."""

    # --- acceptance cases ---------------------------------------------------

    def test_absent_field_treated_as_v1(self) -> None:
        """Missing schema_version key is treated as version 1 (legacy compat)."""
        # Arrange
        counter: dict[str, int] = {}

        # Act
        result = check_schema_version(
            {}, envelope_name="A", expected=1, counter=counter
        )

        # Assert
        assert result is True
        assert counter == {}

    def test_exact_match_accepts(self) -> None:
        """Payload version == expected version → accepted."""
        # Arrange
        counter: dict[str, int] = {}

        # Act
        result = check_schema_version(
            {"schema_version": 1},
            envelope_name="A",
            expected=1,
            counter=counter,
        )

        # Assert
        assert result is True
        assert counter == {}

    def test_receiver_newer_accepts(self) -> None:
        """Payload version < expected → accepted (receiver upgraded first)."""
        # Arrange
        counter: dict[str, int] = {}

        # Act
        result = check_schema_version(
            {"schema_version": 1},
            envelope_name="A",
            expected=2,
            counter=counter,
        )

        # Assert
        assert result is True
        assert counter == {}

    # --- drop cases ---------------------------------------------------------

    def test_receiver_older_drops(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Payload version > expected → dropped, counter++, log.error emitted."""
        # Arrange
        counter: dict[str, int] = {}

        # Act
        with caplog.at_level(logging.ERROR, logger="lyra.nats._version_check"):
            result = check_schema_version(
                {"schema_version": 2},
                envelope_name="InboundMessage",
                expected=1,
                counter=counter,
            )

        # Assert — return value
        assert result is False
        # Assert — counter incremented
        assert counter == {"InboundMessage": 1}
        # Assert — exactly one ERROR log with the expected substring
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(error_records) == 1
        assert "NATS schema version mismatch" in error_records[0].getMessage()

    def test_string_value_drops(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """String schema_version (malformed) → dropped, counter++, log.error."""
        # Arrange
        counter: dict[str, int] = {}

        # Act
        with caplog.at_level(logging.ERROR, logger="lyra.nats._version_check"):
            result = check_schema_version(
                {"schema_version": "1"},
                envelope_name="InboundMessage",
                expected=1,
                counter=counter,
            )

        # Assert
        assert result is False
        assert counter == {"InboundMessage": 1}
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(error_records) == 1
        assert "NATS schema version mismatch" in error_records[0].getMessage()

    def test_null_value_drops(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Explicit null schema_version → dropped, counter++, log.error."""
        # Arrange
        counter: dict[str, int] = {}

        # Act
        with caplog.at_level(logging.ERROR, logger="lyra.nats._version_check"):
            result = check_schema_version(
                {"schema_version": None},
                envelope_name="InboundMessage",
                expected=1,
                counter=counter,
            )

        # Assert
        assert result is False
        assert counter == {"InboundMessage": 1}
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(error_records) == 1

    @pytest.mark.parametrize("bad_version", [0, -1])
    def test_zero_or_negative_drops(
        self, bad_version: int, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Integer <= 0 → dropped (invalid range), counter++, log.error."""
        # Arrange
        counter: dict[str, int] = {}

        # Act
        with caplog.at_level(logging.ERROR, logger="lyra.nats._version_check"):
            result = check_schema_version(
                {"schema_version": bad_version},
                envelope_name="InboundMessage",
                expected=1,
                counter=counter,
            )

        # Assert
        assert result is False
        assert counter == {"InboundMessage": 1}
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(error_records) == 1
        assert "NATS schema version mismatch" in error_records[0].getMessage()

    # --- isolation -----------------------------------------------------------

    def test_counter_isolation(self) -> None:
        """Two separate counter dicts accumulate only their own drops."""
        # Arrange
        counter_a: dict[str, int] = {}
        counter_b: dict[str, int] = {}

        # Act — both calls are mismatch drops (payload v2, expected v1)
        check_schema_version(
            {"schema_version": 2},
            envelope_name="EnvelopeA",
            expected=1,
            counter=counter_a,
        )
        check_schema_version(
            {"schema_version": 2},
            envelope_name="EnvelopeB",
            expected=1,
            counter=counter_b,
        )

        # Assert — each dict holds only its own key, not the other's
        assert counter_a == {"EnvelopeA": 1}
        assert counter_b == {"EnvelopeB": 1}
        assert "EnvelopeB" not in counter_a
        assert "EnvelopeA" not in counter_b
