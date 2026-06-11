"""Tests for roxabi_contracts.envelope.ContractEnvelope and WorkEnvelope."""

from datetime import datetime, timezone

import pytest

from roxabi_contracts import (
    CONTRACT_VERSION,
    ContractEnvelope,
    WorkEnvelope,
    new_job_id,
)
from roxabi_contracts.jobs.models import JobEnvelope
from roxabi_contracts.turns.models import TurnWriteEvent


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
    (ADR-044 (absorbed into ADR-049) §Wire-protocol contract).
    A silent change must fail a test so the bump is only ever deliberate.
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


# ---------------------------------------------------------------------------
# WorkEnvelope tests
# ---------------------------------------------------------------------------


def _base_fields() -> dict:
    return {
        "contract_version": "1",
        "trace_id": "t-001",
        "issued_at": datetime.now(timezone.utc),
    }


def test_work_envelope_is_subclass_of_contract_envelope() -> None:
    assert issubclass(WorkEnvelope, ContractEnvelope)


def test_new_job_id_returns_32_char_hex() -> None:
    jid = new_job_id()
    assert isinstance(jid, str)
    assert len(jid) == 32
    assert all(c in "0123456789abcdef" for c in jid)


def test_work_envelope_auto_mints_job_id_when_omitted() -> None:
    """TRANSITIONAL: job_id defaults via new_job_id() when not supplied."""
    env = WorkEnvelope(**_base_fields())
    assert len(env.job_id) == 32


def test_work_envelope_explicit_job_id_accepted() -> None:
    env = WorkEnvelope(**_base_fields(), job_id="abc123")
    assert env.job_id == "abc123"


def test_work_envelope_empty_job_id_raises() -> None:
    """Empty string is explicitly passed — must raise (SC-3)."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        WorkEnvelope(**_base_fields(), job_id="")


def test_work_envelope_invalid_job_id_raises() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        WorkEnvelope(**_base_fields(), job_id="bad..token")


def test_work_envelope_parent_job_id_default_none() -> None:
    env = WorkEnvelope(**_base_fields())
    assert env.parent_job_id is None


def test_work_envelope_parent_job_id_accepted() -> None:
    env = WorkEnvelope(**_base_fields(), parent_job_id="parent-001")
    assert env.parent_job_id == "parent-001"


def test_work_envelope_invalid_parent_job_id_raises() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        WorkEnvelope(**_base_fields(), parent_job_id="bad token!")


def test_work_envelope_wire_compat_missing_job_id() -> None:
    """Pre-#1619 wire message without job_id must parse (TRANSITIONAL default)."""
    payload = {
        "contract_version": "1",
        "trace_id": "t-wire",
        "issued_at": "2026-04-16T12:00:00+00:00",
        # no job_id — pre-#1619 wire message
    }
    env = WorkEnvelope.model_validate(payload)
    assert len(env.job_id) == 32  # auto-minted


# ---------------------------------------------------------------------------
# Wire-compat: concrete WorkEnvelope subclasses parse without job_id
# ---------------------------------------------------------------------------


def test_turn_write_event_wire_compat_missing_job_id() -> None:
    """TurnWriteEvent (concrete WorkEnvelope) parses without job_id — TRANSITIONAL.

    Forward-compat invariant: a pre-#1619 TurnWriteEvent wire message that
    omits job_id must still deserialize cleanly.  The minted id confirms the
    default_factory is exercised on the subclass, not only on the base class.
    """
    from datetime import datetime, timezone
    from uuid import uuid4

    payload = {
        "contract_version": "1",
        "trace_id": "t-turn-wire",
        "issued_at": "2026-04-16T12:00:00+00:00",
        "event_id": str(uuid4()),
        "pool_id": "pool:tg:chat:1",
        "session_id": "sess-wire-0001",
        "platform": "telegram",
        "user_id": "u:wire:1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "payload": {"kind": "start_session"},
        # job_id intentionally absent
    }
    event = TurnWriteEvent.model_validate(payload)
    assert event.job_id
    assert len(event.job_id) == 32


def test_job_envelope_wire_compat_missing_job_id() -> None:
    """JobEnvelope (concrete WorkEnvelope) parses without job_id — TRANSITIONAL."""
    payload = {
        "contract_version": "1",
        "trace_id": "t-job-wire",
        "issued_at": "2026-04-16T12:00:00+00:00",
        "job_name": "summarize",
        "payload": {},
        "reply_to": "_INBOX.test",
        # job_id intentionally absent
    }
    env = JobEnvelope.model_validate(payload)
    assert env.job_id
    assert len(env.job_id) == 32
