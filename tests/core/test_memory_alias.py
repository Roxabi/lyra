"""Tests for MemoryManager alias-awareness — cross-platform recall (#472).

Retired: MemoryManager vault backend removed — cortex ADR-087.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio

from factory.core.memory.memory import MemoryManager, SessionSnapshot
from factory.infrastructure.stores.identity.identity_alias_store import (
    IdentityAliasStore,
)

pytestmark = pytest.mark.skip(
    reason="MemoryManager vault backend removed — cortex ADR-087"
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_snap(
    user_id: str = "tg:user:1",
    session_id: str = "sess-1",
    agent_namespace: str = "lyra",
) -> SessionSnapshot:
    return SessionSnapshot(
        session_id=session_id,
        user_id=user_id,
        medium="telegram",
        agent_namespace=agent_namespace,
        session_start=datetime.now(UTC),
        session_end=datetime.now(UTC),
        message_count=1,
        source_turns=0,
    )


@pytest_asyncio.fixture
async def mm(tmp_path: Path) -> MemoryManager:
    """Real MemoryManager backed by an in-memory SQLite DB."""
    manager = MemoryManager(":memory:")
    await manager.connect()
    yield manager
    await manager.close()


@pytest_asyncio.fixture
async def alias_store(tmp_path: Path) -> IdentityAliasStore:
    store = IdentityAliasStore(tmp_path / "alias.db")
    await store.connect()
    yield store
    await store.close()


class TestAliasRecall:
    async def test_placeholder(
        self, mm: MemoryManager, alias_store: IdentityAliasStore
    ) -> None:
        assert mm is not None
