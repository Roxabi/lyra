"""Regression guard: backward-compat re-export shims still resolve to canonical classes.

Verifies that the ADR-059 V6 shims in roxabi-contracts forward correctly to
the canonical implementations in roxabi_nats.testing.
"""

from __future__ import annotations

import pytest

from roxabi_nats.testing._guards import assert_loopback_url as _canonical_loopback
from roxabi_nats.testing._guards import assert_not_production as _canonical_prod
from roxabi_nats.testing.image import FakeImageWorker as _CanonicalFakeImageWorker
from roxabi_nats.testing.voice import FakeSttWorker as _CanonicalFakeSttWorker
from roxabi_nats.testing.voice import FakeTtsWorker as _CanonicalFakeTtsWorker


def test_voice_testing_shim_reexports() -> None:
    from roxabi_contracts.voice.testing import FakeSttWorker, FakeTtsWorker

    assert FakeTtsWorker is _CanonicalFakeTtsWorker
    assert FakeSttWorker is _CanonicalFakeSttWorker


def test_image_testing_shim_reexports() -> None:
    from roxabi_contracts.image.testing import FakeImageWorker

    assert FakeImageWorker is _CanonicalFakeImageWorker


def test_testing_guards_shim_reexports() -> None:
    with pytest.warns(
        DeprecationWarning, match="roxabi_contracts._testing_guards is deprecated"
    ):
        from roxabi_contracts._testing_guards import (
            assert_loopback_url,
            assert_not_production,
        )

    assert assert_not_production is _canonical_prod
    assert assert_loopback_url is _canonical_loopback
