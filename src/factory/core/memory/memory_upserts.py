"""Upsert (write) methods for MemoryManager — split from memory.py (epic #293).

Retired with roxabi-vault removal. Methods remain as stubs so inheritance
shape is stable until a cortex-backed MemoryManager lands (ADR-087).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from factory.core.memory.memory_types import SessionSnapshot

_REMOVED_MSG = (
    "MemoryManager vault backend removed — use cortex-memory (ADR-087)."
)


class MemoryManagerUpserts:
    """Retired write mixin — all methods raise."""

    if TYPE_CHECKING:

        async def get_identity_anchor(self, namespace: str) -> str | None: ...

    async def save_identity_anchor(self, namespace: str, text: str) -> None:
        raise RuntimeError(_REMOVED_MSG)

    async def upsert_session(
        self,
        snap: SessionSnapshot,
        summary: str,
        status: str = "final",
    ) -> None:
        raise RuntimeError(_REMOVED_MSG)

    async def upsert_contact(self, user_id: str, medium: str, namespace: str) -> None:
        raise RuntimeError(_REMOVED_MSG)

    async def upsert_concept(self, snap: SessionSnapshot, data: dict) -> None:
        raise RuntimeError(_REMOVED_MSG)

    async def upsert_preference(self, snap: SessionSnapshot, data: dict) -> None:
        raise RuntimeError(_REMOVED_MSG)
