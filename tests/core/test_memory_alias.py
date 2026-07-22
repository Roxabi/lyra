"""Tests for MemoryManager alias-awareness — cross-platform recall (#472).

Retired: MemoryManager vault backend removed — cortex ADR-087.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(
    reason="MemoryManager vault backend removed — cortex ADR-087"
)


def test_memory_alias_retired() -> None:
    """Placeholder so the module collects under pytestmark skip."""
    assert True
