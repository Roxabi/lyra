"""Integration: put_soul_document warms cache → agent_row_to_config composes."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from factory.core.agent.agent_db_loader import agent_row_to_config
from factory.core.agent.agent_models import AgentRow
from factory.core.agent.soul_cache import get_soul_document_cache
from factory.infrastructure.soul.soul_ops import put_soul_document
from factory.infrastructure.stores.registry.agent_store import AgentStore
from roxabi_contracts.blob_ref import BlobRef

_SOUL_MD = """## Identity
Loader path Lyra
## Personality
Calm
## Values
Honest
## Expertise
Python
## Guidelines
Test real put
"""


class _MemoryBlobStore:
    def __init__(self) -> None:
        self._data: dict[str, bytes] = {}

    async def put(  # noqa: PLR0913
        self,
        data: bytes,
        *,
        mime: str,
        source: str,
        filename: str | None = None,
        platform_ref: str | None = None,
        platform_message_id: str | None = None,
    ) -> BlobRef:
        key = f"sha256:{len(self._data):064x}"
        self._data[key] = data
        return BlobRef(
            store_key=key,
            content_hash=key.split(":", 1)[1],
            mime=mime,
            size=len(data),
            source=source,
        )

    async def get(self, store_key: str) -> bytes:
        return self._data[store_key]

    async def exists(self, content_hash: str) -> BlobRef | None:
        for key in self._data:
            if key.endswith(content_hash) or content_hash in key:
                return BlobRef(
                    store_key=key,
                    content_hash=content_hash,
                    mime="text/markdown",
                    size=len(self._data[key]),
                    source="soul",
                )
        return None


@pytest.mark.asyncio
async def test_put_soul_document_then_loader_without_manual_cache_seed() -> None:
    get_soul_document_cache().invalidate("lyra")
    blob = _MemoryBlobStore()

    with tempfile.TemporaryDirectory() as tmpdir:
        store = AgentStore(Path(tmpdir) / "agents.db")
        await store.connect()
        try:
            await store.upsert(
                AgentRow(name="lyra", backend="claude-cli", model="sonnet")
            )
            updated = await put_soul_document(blob, store, "lyra", _SOUL_MD)
            assert updated.soul_document_blob_ref

            # Loader reads write-through cache populated by put_soul_document.
            row = store.get("lyra")
            assert row is not None
            agent = agent_row_to_config(row)
            assert "Loader path Lyra" in agent.system_prompt
            assert "Test real put" in agent.system_prompt
        finally:
            await store.close()