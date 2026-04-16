"""Tests for roxabi_contracts.envelope.ContractEnvelope."""

from datetime import datetime, timezone

from roxabi_contracts import ContractEnvelope


def test_instantiation_with_required_fields() -> None:
    env = ContractEnvelope(
        contract_version="1",
        trace_id="abc-123",
        issued_at=datetime.now(timezone.utc),
    )
    assert env.contract_version == "1"
    assert env.trace_id == "abc-123"
    assert isinstance(env.issued_at, datetime)


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
