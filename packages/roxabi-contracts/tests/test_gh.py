"""Tests for roxabi_contracts.gh domain models and subjects.

Covers MintFailureEvent round-trip, field validation boundaries, extra-field
ignore, and subject helper correctness.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from roxabi_contracts.gh import MintFailureEvent, gh_mint_failure
from roxabi_contracts.gh.fixtures import sample_mint_failure

# ---------------------------------------------------------------------------
# test_mint_failure_roundtrip
# ---------------------------------------------------------------------------


def test_mint_failure_roundtrip() -> None:
    """model_validate → model_dump_json → model_validate_json yields equal instance."""
    # Arrange / Act
    inst = MintFailureEvent.model_validate(sample_mint_failure)
    restored = MintFailureEvent.model_validate_json(inst.model_dump_json())

    # Assert
    assert restored == inst


# ---------------------------------------------------------------------------
# test_mint_failure_extra_ignore
# ---------------------------------------------------------------------------


def test_mint_failure_extra_ignore() -> None:
    """Unknown fields are silently ignored (extra='ignore') and absent from output."""
    # Arrange
    payload: dict[str, Any] = {**sample_mint_failure, "unknown_field": "surprise"}

    # Act
    inst = MintFailureEvent.model_validate(payload)
    dumped = inst.model_dump()

    # Assert
    assert "unknown_field" not in dumped


# ---------------------------------------------------------------------------
# test_mint_failure_rejects_bad_machine
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "machine",
    [
        pytest.param("bad*machine", id="wildcard-asterisk"),
        pytest.param("bad>machine", id="wildcard-gt"),
        pytest.param(".leading-dot", id="leading-dot"),
        pytest.param("trailing.", id="trailing-dot"),
        pytest.param("a..b", id="consecutive-dots"),
    ],
)
def test_mint_failure_rejects_bad_machine(machine: str) -> None:
    """field_validator on machine rejects wildcards and boundary dot violations."""
    # Arrange
    payload: dict[str, Any] = {**sample_mint_failure, "machine": machine}

    # Act / Assert
    with pytest.raises(ValidationError):
        MintFailureEvent.model_validate(payload)


# ---------------------------------------------------------------------------
# test_mint_failure_http_status_none_allowed
# ---------------------------------------------------------------------------


def test_mint_failure_http_status_none_allowed() -> None:
    """http_status=None is valid (pre-API failure path)."""
    # Arrange
    payload: dict[str, Any] = {**sample_mint_failure, "http_status": None}

    # Act
    inst = MintFailureEvent.model_validate(payload)

    # Assert
    assert inst.http_status is None


# ---------------------------------------------------------------------------
# test_mint_failure_http_status_boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("http_status", "should_raise"),
    [
        pytest.param(99, True, id="99-invalid"),
        pytest.param(100, False, id="100-valid"),
        pytest.param(599, False, id="599-valid"),
        pytest.param(600, True, id="600-invalid"),
    ],
)
def test_mint_failure_http_status_boundary(
    http_status: int, should_raise: bool
) -> None:
    """http_status must be in [100, 599] when set; outside that range raises."""
    # Arrange
    payload: dict[str, Any] = {**sample_mint_failure, "http_status": http_status}

    if should_raise:
        # Act / Assert
        with pytest.raises(ValidationError):
            MintFailureEvent.model_validate(payload)
    else:
        inst = MintFailureEvent.model_validate(payload)
        assert inst.http_status == http_status


# ---------------------------------------------------------------------------
# test_mint_failure_retries_boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("retries", "should_raise"),
    [
        pytest.param(-1, True, id="-1-invalid"),
        pytest.param(0, False, id="0-valid"),
        pytest.param(10, False, id="10-valid"),
        pytest.param(11, True, id="11-invalid"),
    ],
)
def test_mint_failure_retries_boundary(retries: int, should_raise: bool) -> None:
    """retries must be in [0, 10]; outside that range raises ValidationError."""
    # Arrange
    payload: dict[str, Any] = {**sample_mint_failure, "retries": retries}

    if should_raise:
        # Act / Assert
        with pytest.raises(ValidationError):
            MintFailureEvent.model_validate(payload)
    else:
        inst = MintFailureEvent.model_validate(payload)
        assert inst.retries == retries


# ---------------------------------------------------------------------------
# test_mint_failure_reason_empty_rejected
# ---------------------------------------------------------------------------


def test_mint_failure_reason_empty_rejected() -> None:
    """StringConstraints(min_length=1) on reason rejects empty string."""
    # Arrange
    payload: dict[str, Any] = {**sample_mint_failure, "reason": ""}

    # Act / Assert
    with pytest.raises(ValidationError):
        MintFailureEvent.model_validate(payload)


# ---------------------------------------------------------------------------
# test_subject_helper_happy
# ---------------------------------------------------------------------------


def test_subject_helper_happy() -> None:
    """gh_mint_failure produces lyra.gh.mint_failure.<machine>."""
    assert gh_mint_failure("M1") == "lyra.gh.mint_failure.M1"


# ---------------------------------------------------------------------------
# test_subject_helper_rejects_bad_token
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_token",
    [
        pytest.param("bad*token", id="asterisk"),
        pytest.param("bad>token", id="greater-than"),
        pytest.param("", id="empty-string"),
        pytest.param(".leading-dot", id="leading-dot"),
        pytest.param("trailing.", id="trailing-dot"),
        pytest.param("a..b", id="consecutive-dots"),
    ],
)
def test_subject_helper_rejects_bad_token(bad_token: str) -> None:
    """gh_mint_failure raises ValueError for wildcards, empty strings, dot errors."""
    with pytest.raises(ValueError):
        gh_mint_failure(bad_token)
