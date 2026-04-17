"""Stub module used as a (module_path, type_name) target by resolver tests.

Registered in :mod:`sys.modules` under the synthetic name
``roxabi_nats_test_stub`` from this package's ``conftest.py`` so
``_TypeHintResolver([("roxabi_nats_test_stub", "StubInner")])`` resolves
to :class:`StubInner` below — without shipping any test-only file inside
the production wheel (which only packages ``src/roxabi_nats``).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class StubInner:  # noqa: D101
    value: str = "stub"
