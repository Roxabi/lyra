"""RED-phase tests for _TypeHintResolver and clean-break removal of the global
registry (issue #729).

All tests are expected to fail with ImportError until T2 implements
_TypeHintResolver and removes _register_type_checking_import / _TYPE_CHECKING_IMPORTS
from _serialize.py.

Covers:
- (a) non-empty resolver round-trip resolves the stub type
- (b) empty resolver round-trip of a dataclass with no TYPE_CHECKING hints
- (c) duplicate (module, name) entries deduped
- (d) non-existent module raises ValueError at resolver construction
- (e) non-existent attribute raises ValueError at resolver construction
- (f) clean-break guard: _register_type_checking_import and _TYPE_CHECKING_IMPORTS
      are not importable from roxabi_nats._serialize
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from roxabi_nats._serialize import (
    _EMPTY_RESOLVER,
    _TypeHintResolver,
    deserialize,
    serialize,
)

if TYPE_CHECKING:
    from roxabi_nats._test_stub_module import StubInner


@dataclass
class _StubOuter:
    name: str
    inner: "StubInner | None" = None


# ---------------------------------------------------------------------------
# (a) non-empty resolver round-trip
# ---------------------------------------------------------------------------


def test_resolver_resolves_stub_type() -> None:
    """A resolver with the stub module entry correctly round-trips _StubOuter."""
    # Arrange
    r = _TypeHintResolver([("roxabi_nats._test_stub_module", "StubInner")])
    payload = serialize(_StubOuter(name="x"))

    # Act
    round = deserialize(payload, _StubOuter, resolver=r)

    # Assert
    assert round.name == "x"


# ---------------------------------------------------------------------------
# (b) empty resolver — no TYPE_CHECKING hints
# ---------------------------------------------------------------------------


def test_empty_resolver_no_typechecking_hints() -> None:
    """Empty resolver round-trips a plain dataclass with no TYPE_CHECKING fields."""
    # Arrange
    @dataclass
    class Plain:
        n: int

    r = _TypeHintResolver(())

    # Act
    result = deserialize(serialize(Plain(n=5)), Plain, resolver=r)

    # Assert
    assert result.n == 5


# ---------------------------------------------------------------------------
# (c) duplicate entries deduped
# ---------------------------------------------------------------------------


def test_duplicate_entries_deduped() -> None:
    """Duplicate (module, name) pairs collapse to a single entry."""
    # Arrange / Act
    r = _TypeHintResolver([
        ("roxabi_nats._test_stub_module", "StubInner"),
        ("roxabi_nats._test_stub_module", "StubInner"),
    ])

    # Assert
    assert len(r.entries) == 1


# ---------------------------------------------------------------------------
# (d) non-existent module raises ValueError
# ---------------------------------------------------------------------------


def test_non_existent_module_raises() -> None:
    """Constructing with an unimportable module raises ValueError."""
    # Arrange / Act / Assert
    with pytest.raises(ValueError, match="cannot import roxabi_nats._does_not_exist"):
        _TypeHintResolver([("roxabi_nats._does_not_exist", "StubInner")])


# ---------------------------------------------------------------------------
# (e) non-existent attribute raises ValueError
# ---------------------------------------------------------------------------


def test_non_existent_attribute_raises() -> None:
    """Constructing with a missing attribute on a real module raises ValueError."""
    # Arrange / Act / Assert
    with pytest.raises(ValueError, match="has no attribute DoesNotExist"):
        _TypeHintResolver([("roxabi_nats._test_stub_module", "DoesNotExist")])


# ---------------------------------------------------------------------------
# (f) clean-break guard — global registry removed
# ---------------------------------------------------------------------------


def test_clean_break_register_helper_removed() -> None:
    """_register_type_checking_import must not exist on _serialize."""
    # Arrange / Act / Assert
    mod = importlib.import_module("roxabi_nats._serialize")
    assert not hasattr(mod, "_register_type_checking_import")


def test_clean_break_global_registry_removed() -> None:
    """_TYPE_CHECKING_IMPORTS must not exist on _serialize."""
    # Arrange / Act / Assert
    mod = importlib.import_module("roxabi_nats._serialize")
    assert not hasattr(mod, "_TYPE_CHECKING_IMPORTS")


# ---------------------------------------------------------------------------
# _EMPTY_RESOLVER sanity check
# ---------------------------------------------------------------------------


def test_empty_resolver_singleton_is_module_level() -> None:
    """_EMPTY_RESOLVER is a module-level instance with an empty entries tuple."""
    # Arrange / Act / Assert
    assert _EMPTY_RESOLVER.entries == ()
