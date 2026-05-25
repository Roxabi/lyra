"""Tests for roxabi_contracts.turns subjects namespace.

Locks the canonical subject strings used by lyra's TurnStore writer so a typo
or accidental rename fails here, not silently in production.
"""

import subprocess
import sys

from roxabi_contracts.turns.subjects import SUBJECTS


def test_turn_write_subject() -> None:
    assert SUBJECTS.turn_write == "lyra.turns.write"


def test_subjects_is_frozen() -> None:
    """_Subjects dataclass is frozen — mutation must raise FrozenInstanceError."""
    import dataclasses

    import pytest

    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        SUBJECTS.turn_write = "mutated"  # type: ignore[misc]


def test_subjects_has_slots() -> None:
    """_Subjects dataclass uses __slots__ — no __dict__ on the instance."""
    assert not hasattr(SUBJECTS, "__dict__")


def test_turns_init_does_not_pull_nats_transports() -> None:
    """Package surface: turns/__init__.py imports no transport code.

    Fresh subprocess import of ``roxabi_contracts.turns`` must not load any
    ``nats.*`` or ``roxabi_nats.*`` module. Guards the pure-Pydantic
    invariant from a silent regression where a stray transport import
    sneaks into models.py or subjects.py.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import roxabi_contracts.turns, sys; "
                "bad = [m for m in sys.modules if m.startswith('nats') or "
                "m.startswith('roxabi_nats')]; "
                "assert not bad, f'transport modules leaked: {bad!r}'"
            ),
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()


def test_turns_init_does_not_expose_fixtures() -> None:
    """Fixtures are test-only — must NOT be in turns package surface.

    Checked two ways: (1) ``fixtures`` is not advertised in ``__all__``
    (what ``from roxabi_contracts.turns import *`` would see), and (2) a
    subprocess with a fresh interpreter that imports
    ``roxabi_contracts.turns`` does NOT pull ``.fixtures`` into
    ``sys.modules``.
    """
    import roxabi_contracts.turns as turns_mod

    assert "fixtures" not in turns_mod.__all__

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import roxabi_contracts.turns, sys; "
                "assert 'roxabi_contracts.turns.fixtures' not in sys.modules, "
                "'turns/__init__.py must not import fixtures'"
            ),
        ],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()
