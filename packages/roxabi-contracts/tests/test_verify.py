"""Tests for roxabi_contracts.verify subjects + deny-probe helper.

``lyra.verify.deny`` is a reserved sentinel (ungranted negative-control probe).
These tests pin the canonical prefix and the helper's validation boundaries.
"""

from __future__ import annotations

import pytest

from roxabi_contracts.verify import SUBJECTS, verify_deny

# ---------------------------------------------------------------------------
# test_deny_prefix_canonical
# ---------------------------------------------------------------------------


def test_deny_prefix_canonical() -> None:
    """SUBJECTS.deny_prefix is the canonical lyra.verify.deny literal."""
    assert SUBJECTS.deny_prefix == "lyra.verify.deny"


# ---------------------------------------------------------------------------
# test_verify_deny_happy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "identity",
    [
        pytest.param("hub", id="plain"),
        pytest.param("telegram-adapter", id="hyphenated"),
        pytest.param("turn-writer", id="hyphenated-2"),
    ],
)
def test_verify_deny_happy(identity: str) -> None:
    """verify_deny produces lyra.verify.deny.<identity> for valid identity names."""
    assert verify_deny(identity) == f"lyra.verify.deny.{identity}"


# ---------------------------------------------------------------------------
# test_verify_deny_rejects_bad_identity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_identity",
    [
        pytest.param("bad*id", id="asterisk"),
        pytest.param("bad>id", id="greater-than"),
        pytest.param("", id="empty-string"),
        pytest.param("has.dot", id="dot-not-a-segment"),
        pytest.param("has space", id="space"),
    ],
)
def test_verify_deny_rejects_bad_identity(bad_identity: str) -> None:
    """verify_deny raises ValueError for wildcards, dots, spaces, empty string."""
    with pytest.raises(ValueError):
        verify_deny(bad_identity)
