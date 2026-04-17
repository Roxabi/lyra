"""Tests for _TypeHintResolver and clean-break removal of the global registry (#729).

Covers:
- (a) non-empty resolver round-trip resolves the stub type
- (b) empty resolver round-trip of a dataclass with no TYPE_CHECKING hints
- (c) duplicate (module, name) entries deduped
- (d) non-existent module raises ValueError at resolver construction
- (e) non-existent attribute raises ValueError at resolver construction
- (f) clean-break guard: _register_type_checking_import and _TYPE_CHECKING_IMPORTS
      are not importable from roxabi_nats._serialize
- (g) hint cache isolates resolvers (no cross-resolver poisoning)
- (h) duplicate type_name with different module_path rejected
"""

from __future__ import annotations

import importlib
import types
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
    # `roxabi_nats_test_stub` is registered in `sys.modules` at test-collection
    # time by this package's conftest.py — it does NOT exist on disk under that
    # name, so pyright cannot statically resolve it and we suppress the import
    # error. Runtime imports still work everywhere the stub is actually needed.
    from roxabi_nats_test_stub import StubInner  # type: ignore[import-not-found]


@dataclass
class _StubOuter:
    name: str
    inner: "StubInner | None" = None


# ---------------------------------------------------------------------------
# (a) non-empty resolver round-trip
# ---------------------------------------------------------------------------


def test_resolver_resolves_stub_type() -> None:
    """A resolver with the stub module entry correctly round-trips _StubOuter.

    Exercises resolver-driven type coercion: the inner field is typed as
    StubInner under TYPE_CHECKING only; the resolver provides it at runtime
    so deserialize can reconstruct the nested dataclass from a raw dict.
    """
    # Arrange
    from roxabi_nats_test_stub import (
        StubInner,  # noqa: PLC0415  # type: ignore[import-not-found]
    )

    r = _TypeHintResolver([("roxabi_nats_test_stub", "StubInner")])
    payload = serialize(_StubOuter(name="x", inner=StubInner()))

    # Act
    result = deserialize(payload, _StubOuter, resolver=r)

    # Assert
    assert result.name == "x"
    assert isinstance(result.inner, StubInner)


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
    r = _TypeHintResolver(
        [
            ("roxabi_nats_test_stub", "StubInner"),
            ("roxabi_nats_test_stub", "StubInner"),
        ]
    )

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
        _TypeHintResolver([("roxabi_nats_test_stub", "DoesNotExist")])


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
    """_EMPTY_RESOLVER is a module-level instance with an empty entries tuple,
    an immutable MappingProxyType resolved dict, and rejects mutation.
    """
    # Assert entries empty
    assert _EMPTY_RESOLVER.entries == ()
    # Assert resolved is a MappingProxyType (immutable)
    assert isinstance(_EMPTY_RESOLVER.resolved, types.MappingProxyType)
    # Assert mutation raises TypeError
    with pytest.raises(TypeError):
        _EMPTY_RESOLVER.resolved["x"] = 1  # type: ignore[index]


# ---------------------------------------------------------------------------
# Cache isolation across resolvers
# ---------------------------------------------------------------------------


def test_hints_cache_isolated_across_resolvers() -> None:
    """resolver._uid belongs to _hints_cache key — resolvers do not poison each other.

    Empty resolver leaves inner uncoerced (NameError fallback → {}).  Non-empty
    resolver reconstructs StubInner.  Order: non-empty first, then empty.
    """
    from roxabi_nats_test_stub import (
        StubInner,  # noqa: PLC0415  # type: ignore[import-not-found]
    )

    r_non_empty = _TypeHintResolver([("roxabi_nats_test_stub", "StubInner")])
    r_empty = _TypeHintResolver(())

    inner = StubInner()
    payload = serialize(_StubOuter(name="cache-test", inner=inner))

    # Non-empty resolver: StubInner resolved → inner is a StubInner instance
    result_full = deserialize(payload, _StubOuter, resolver=r_non_empty)
    assert isinstance(result_full.inner, StubInner)

    # Empty resolver: StubInner not in scope → inner stays as raw dict (not coerced)
    result_empty = deserialize(payload, _StubOuter, resolver=r_empty)
    assert not isinstance(result_empty.inner, StubInner)


def test_hints_cache_no_poisoning_when_empty_runs_first() -> None:
    """Mirror of the isolation test — run the empty resolver FIRST.

    Canonical cache-poisoning scenario: under a dc_type-only cache key, the
    empty resolver caches `{}` for `_StubOuter`, then the non-empty resolver
    hits the cached empty hints and fails to coerce StubInner. The per-UID
    cache key prevents this; the test asserts the second resolver still sees
    correct coercion regardless of ordering.
    """
    from roxabi_nats_test_stub import (
        StubInner,  # noqa: PLC0415  # type: ignore[import-not-found]
    )

    r_empty = _TypeHintResolver(())
    r_non_empty = _TypeHintResolver([("roxabi_nats_test_stub", "StubInner")])

    payload = serialize(_StubOuter(name="mirror", inner=StubInner()))

    # Empty FIRST (would poison a dc_type-only cache)
    result_empty = deserialize(payload, _StubOuter, resolver=r_empty)
    assert not isinstance(result_empty.inner, StubInner)

    # Non-empty SECOND — per-UID cache isolation must still give us StubInner
    result_full = deserialize(payload, _StubOuter, resolver=r_non_empty)
    assert isinstance(result_full.inner, StubInner)


# ---------------------------------------------------------------------------
# Duplicate type_name collision detection
# ---------------------------------------------------------------------------


def test_type_registry_duplicate_type_name_rejected() -> None:
    """Same type_name with different module_path raises ValueError (fail-loud)."""
    with pytest.raises(ValueError, match="duplicate type_name"):
        _TypeHintResolver(
            [
                ("roxabi_nats_test_stub", "StubInner"),
                ("nonexistent.path", "StubInner"),
            ]
        )
