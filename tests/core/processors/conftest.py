"""Shared fixtures for processor tests.

Autouse: clear the global ProcessorRegistry before each test to prevent
cross-test contamination when lyra.core.processors has already been imported
and registered its built-in commands (B11).
"""

from __future__ import annotations

from collections.abc import Generator

import pytest

from lyra.core.processor_registry import registry


@pytest.fixture(autouse=True)
def _clear_processor_registry() -> Generator[None, None, None]:
    """Reset the global registry before every processor test."""
    registry.clear()
    yield
    registry.clear()
