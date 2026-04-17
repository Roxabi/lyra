"""Tests for roxabi_contracts.envelope.ContractEnvelope."""

from datetime import datetime, timezone

from roxabi_contracts import CONTRACT_VERSION, ContractEnvelope


def test_contract_version_is_positive_digit() -> None:
    """Invariant: CONTRACT_VERSION MUST remain a positive decimal string.

    The module-level assert in ``envelope.py`` enforces this at import
    time; this test locks the invariant as an explicit characterization
    so any future refactor that weakens the assert surfaces as a test
    failure rather than a runtime drop of every inbound envelope.
    """
    assert isinstance(CONTRACT_VERSION, str)
    assert CONTRACT_VERSION.isdigit()
    assert int(CONTRACT_VERSION) > 0


def test_contract_version_current_value() -> None:
    """Lock the current value against accidental drift.

    Bumping ``CONTRACT_VERSION`` is a cross-repo coordination event
    (ADR-044 §Wire-protocol contract). A silent change must fail a test
    so the bump is only ever deliberate.
    """
    assert CONTRACT_VERSION == "1"


def test_instantiation_with_required_fields() -> None:
    env = ContractEnvelope(
        contract_version="1",
        trace_id="abc-123",
        issued_at=datetime.now(timezone.utc),
    )
    assert env.contract_version == "1"
    assert env.trace_id == "abc-123"
    assert isinstance(env.issued_at, datetime)


def test_naive_datetime_string_is_accepted_without_timezone() -> None:
    """Characterize current permissive behavior on naive ISO datetime strings.

    ContractEnvelope declares ``issued_at: datetime`` with no timezone
    constraint at the base layer — per-domain subclasses may tighten.
    Pydantic v2 accepts naive ISO strings and produces ``datetime``
    objects with ``tzinfo is None``. This test locks that behavior so
    any future ``@field_validator`` enforcing tz-awareness surfaces as
    a test failure rather than a silent downstream breakage.
    """
    env = ContractEnvelope.model_validate(
        {
            "contract_version": "1",
            "trace_id": "abc-123",
            "issued_at": "2026-04-16T12:00:00",
        }
    )
    assert env.issued_at.tzinfo is None


def test_extra_fields_silently_dropped() -> None:
    """Forward-compat invariant: unknown fields MUST be dropped, not raise.

    ADR-049 §Versioning: a v0.1.0 satellite receiving a v0.2.0 payload
    with a new optional field parses cleanly.
    """
    env = ContractEnvelope.model_validate(
        {
            "contract_version": "1",
            "trace_id": "abc-123",
            "issued_at": "2026-04-16T12:00:00+00:00",
            "future_field": "this should not raise",
            "another_unknown": 42,
        }
    )
    assert env.contract_version == "1"
    assert not hasattr(env, "future_field")
    assert not hasattr(env, "another_unknown")
