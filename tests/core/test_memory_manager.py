"""Tests for MemoryManager and related data structures (issue #83 S2-S7).

Retired: MemoryManager vault backend removed — cortex ADR-087.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(
    reason="MemoryManager vault backend removed — cortex ADR-087"
)


def test_memory_manager_retired() -> None:
    """Placeholder so the module collects under pytestmark skip."""
    assert True
