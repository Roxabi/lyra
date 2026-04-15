"""Unit tests for the check_schema_version helper (issue #530).

Covers all rules described in _version_check.py:
- Missing field → v1 (legacy backwards compat)
- Exact match → accepted
- Receiver newer than payload → accepted (forward-compat)
- Receiver older than payload → dropped (mismatch)
- Non-int values (string, None, zero, negative) → dropped
- Counter isolation between independent callers
- Log-flood rate limiting across repeated drops
"""

from __future__ import annotations

import logging

import pytest

from lyra.nats import _version_check
from lyra.nats._version_check import check_contract_version, check_schema_version

# Rate-limit state is reset before every test via an autouse fixture in
# tests/conftest.py — no per-file reset needed here.

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

    def test_receiver_older_drops(self, caplog: pytest.LogCaptureFixture) -> None:
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

    def test_string_value_drops(self, caplog: pytest.LogCaptureFixture) -> None:
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

    def test_null_value_drops(self, caplog: pytest.LogCaptureFixture) -> None:
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

    @pytest.mark.parametrize("bad_version", [0, -1, 1.0, 2.0])
    def test_malformed_numeric_values_drop(
        self, bad_version: int | float, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Integer <= 0 or float → dropped (invalid), counter++, log.error."""
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


# ---------------------------------------------------------------------------
# TestLogRateLimit — rate limiting of ERROR logs on repeated drops
# ---------------------------------------------------------------------------


class TestLogRateLimit:
    """Verify that drop logs are rate-limited per envelope name."""

    def test_first_drop_logs_at_error(self, caplog: pytest.LogCaptureFixture) -> None:
        """First drop for an envelope fires a single log.error line."""
        # Arrange
        counter: dict[str, int] = {}

        # Act
        with caplog.at_level(logging.ERROR, logger="lyra.nats._version_check"):
            check_schema_version(
                {"schema_version": 2},
                envelope_name="InboundMessage",
                expected=1,
                counter=counter,
            )

        # Assert
        assert counter == {"InboundMessage": 1}
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(error_records) == 1
        assert "NATS schema version mismatch" in error_records[0].getMessage()

    def test_subsequent_drops_within_interval_silent(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Repeat drops within the interval still count but do not log at ERROR."""
        # Arrange
        counter: dict[str, int] = {}

        # Act — three drops in quick succession (well under _LOG_INTERVAL_S)
        with caplog.at_level(logging.ERROR, logger="lyra.nats._version_check"):
            for _ in range(3):
                check_schema_version(
                    {"schema_version": 2},
                    envelope_name="InboundMessage",
                    expected=1,
                    counter=counter,
                )

        # Assert — counter still increments on every drop (rate limit is log-only)
        assert counter == {"InboundMessage": 3}
        # Assert — exactly one ERROR log fired, not three
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(error_records) == 1

    def test_drop_after_interval_logs_again(
        self,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """After _LOG_INTERVAL_S elapses, the next drop fires a fresh ERROR log."""
        # Arrange — monkey-patch time.monotonic so we can simulate elapsed time
        fake_now = [1000.0]

        def fake_monotonic() -> float:
            return fake_now[0]

        monkeypatch.setattr(_version_check.time, "monotonic", fake_monotonic)

        # Act — first drop at t=1000, second drop at t=1000+interval+1
        with caplog.at_level(logging.ERROR, logger="lyra.nats._version_check"):
            check_schema_version(
                {"schema_version": 2},
                envelope_name="InboundMessage",
                expected=1,
            )
            fake_now[0] += _version_check._LOG_INTERVAL_S + 1.0
            check_schema_version(
                {"schema_version": 2},
                envelope_name="InboundMessage",
                expected=1,
            )

        # Assert — both drops fired an ERROR log
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(error_records) == 2

    def test_per_envelope_rate_limit_isolation(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """One envelope's rate-limited silence does not suppress another's first log."""
        # Arrange
        # Act — drop EnvelopeA twice, then EnvelopeB once
        with caplog.at_level(logging.ERROR, logger="lyra.nats._version_check"):
            check_schema_version(
                {"schema_version": 2},
                envelope_name="EnvelopeA",
                expected=1,
            )
            check_schema_version(
                {"schema_version": 2},
                envelope_name="EnvelopeA",
                expected=1,
            )
            check_schema_version(
                {"schema_version": 2},
                envelope_name="EnvelopeB",
                expected=1,
            )

        # Assert — EnvelopeA fires once (rate-limited on second), EnvelopeB fires
        # once (first drop, not silenced by EnvelopeA's limit) → 2 logs total
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(error_records) == 2
        messages = [r.getMessage() for r in error_records]
        assert any("envelope=EnvelopeA" in m for m in messages)
        assert any("envelope=EnvelopeB" in m for m in messages)

    def test_counter_increments_even_when_log_silenced(self) -> None:
        """Rate limiting suppresses logs but never suppresses counter increments."""
        # Arrange
        counter: dict[str, int] = {}

        # Act — 5 drops; only 1 log expected, but all 5 must count
        for _ in range(5):
            check_schema_version(
                {"schema_version": 2},
                envelope_name="InboundMessage",
                expected=1,
                counter=counter,
            )

        # Assert
        assert counter == {"InboundMessage": 5}

    def test_boolean_value_drops(self, caplog: pytest.LogCaptureFixture) -> None:
        """JSON true/false → dropped (bool is int subclass but invalid)."""
        # Arrange
        counter: dict[str, int] = {}

        # Act
        with caplog.at_level(logging.ERROR, logger="lyra.nats._version_check"):
            check_schema_version(
                {"schema_version": True},
                envelope_name="InboundMessage",
                expected=1,
                counter=counter,
            )

        # Assert — True is technically isinstance(_, int) but we treat bool as malformed
        assert counter == {"InboundMessage": 1}
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(error_records) == 1


# ---------------------------------------------------------------------------
# TestCheckContractVersion — issue #707
# ---------------------------------------------------------------------------


class TestCheckContractVersion:
    """Unit tests for check_contract_version()."""

    # --- acceptance cases ---------------------------------------------------

    def test_absent_field_treated_as_v1(self) -> None:
        """Missing contract_version key is treated as "1" (legacy compat)."""
        counter: dict[str, int] = {}

        result = check_contract_version(
            {}, envelope_name="A", expected="1", counter=counter
        )

        assert result is True
        assert counter == {}

    def test_exact_match_accepts(self) -> None:
        """Payload contract_version == expected → accepted."""
        counter: dict[str, int] = {}

        result = check_contract_version(
            {"contract_version": "1"},
            envelope_name="A",
            expected="1",
            counter=counter,
        )

        assert result is True
        assert counter == {}

    def test_receiver_newer_accepts(self) -> None:
        """Payload contract_version < expected → accepted (receiver upgraded first)."""
        counter: dict[str, int] = {}

        result = check_contract_version(
            {"contract_version": "1"},
            envelope_name="A",
            expected="2",
            counter=counter,
        )

        assert result is True
        assert counter == {}

    def test_int_payload_accepts(self) -> None:
        """Payload contract_version given as int (not str) is still accepted."""
        counter: dict[str, int] = {}

        result = check_contract_version(
            {"contract_version": 1},
            envelope_name="A",
            expected="1",
            counter=counter,
        )

        assert result is True
        assert counter == {}

    # --- drop cases ---------------------------------------------------------

    def test_receiver_older_drops(self, caplog: pytest.LogCaptureFixture) -> None:
        """Payload contract_version > expected → dropped, counter++, log.error."""
        counter: dict[str, int] = {}

        with caplog.at_level(logging.ERROR, logger="lyra.nats._version_check"):
            result = check_contract_version(
                {"contract_version": "2"},
                envelope_name="InboundMessage",
                expected="1",
                counter=counter,
            )

        assert result is False
        assert counter == {"InboundMessage": 1}
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(error_records) == 1
        assert "NATS contract version mismatch" in error_records[0].getMessage()

    def test_non_numeric_string_drops(self, caplog: pytest.LogCaptureFixture) -> None:
        """Non-numeric contract_version string → dropped."""
        counter: dict[str, int] = {}

        with caplog.at_level(logging.ERROR, logger="lyra.nats._version_check"):
            result = check_contract_version(
                {"contract_version": "v1"},
                envelope_name="InboundMessage",
                expected="1",
                counter=counter,
            )

        assert result is False
        assert counter == {"InboundMessage": 1}

    def test_null_value_drops(self) -> None:
        """Explicit null contract_version → dropped (no legacy-compat fallback)."""
        counter: dict[str, int] = {}

        result = check_contract_version(
            {"contract_version": None},
            envelope_name="InboundMessage",
            expected="1",
            counter=counter,
        )

        assert result is False
        assert counter == {"InboundMessage": 1}

    @pytest.mark.parametrize("bad_version", [0, -1, "0", "-1"])
    def test_out_of_range_drops(self, bad_version: int | str) -> None:
        """Parsed int <= 0 → dropped."""
        counter: dict[str, int] = {}

        result = check_contract_version(
            {"contract_version": bad_version},
            envelope_name="InboundMessage",
            expected="1",
            counter=counter,
        )

        assert result is False
        assert counter == {"InboundMessage": 1}

    def test_boolean_value_drops(self) -> None:
        """JSON true/false → dropped (bool is int subclass but invalid)."""
        counter: dict[str, int] = {}

        result = check_contract_version(
            {"contract_version": True},
            envelope_name="InboundMessage",
            expected="1",
            counter=counter,
        )

        assert result is False
        assert counter == {"InboundMessage": 1}

    def test_float_value_drops(self) -> None:
        """Float contract_version → dropped."""
        counter: dict[str, int] = {}

        result = check_contract_version(
            {"contract_version": 1.5},
            envelope_name="InboundMessage",
            expected="1",
            counter=counter,
        )

        assert result is False
        assert counter == {"InboundMessage": 1}

    def test_malformed_hub_expected_drops(self) -> None:
        """Non-parseable hub expected value → defensive drop, never silent accept."""
        counter: dict[str, int] = {}

        result = check_contract_version(
            {"contract_version": "1"},
            envelope_name="InboundMessage",
            expected="not-a-number",
            counter=counter,
        )

        assert result is False
        assert counter == {"InboundMessage": 1}

    def test_counter_isolation(self) -> None:
        """Two separate counter dicts accumulate only their own drops."""
        counter_a: dict[str, int] = {}
        counter_b: dict[str, int] = {}

        check_contract_version(
            {"contract_version": "2"},
            envelope_name="EnvelopeA",
            expected="1",
            counter=counter_a,
        )
        check_contract_version(
            {"contract_version": "2"},
            envelope_name="EnvelopeB",
            expected="1",
            counter=counter_b,
        )

        assert counter_a == {"EnvelopeA": 1}
        assert counter_b == {"EnvelopeB": 1}
