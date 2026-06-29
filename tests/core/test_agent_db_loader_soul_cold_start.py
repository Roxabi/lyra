"""Cold-start: preload from blobstore → agent_row_to_config without manual warm."""

from __future__ import annotations

import pytest

from factory.core.agent.agent_db_loader import agent_row_to_config
from factory.core.agent.agent_models import AgentRow
from factory.core.agent.soul_cache import get_soul_document_cache
from factory.infrastructure.soul.soul_ops import preload_soul_caches_for_rows
from roxabi_contracts.blob_ref import BlobRef

_SOUL_MD = """## Identity
Cold start Lyra
## Personality
Calm
## Values
Honest
## Expertise
Python
## Guidelines
Bootstrap preload
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
async def test_preload_then_loader_without_manual_warm_soul_cache() -> None:
    get_soul_document_cache().invalidate("lyra")
    blob = _MemoryBlobStore()
    ref = await blob.put(_SOUL_MD.encode("utf-8"), mime="text/markdown", source="soul")
    store_key = ref.store_key
    assert store_key

    row = AgentRow(
        name="lyra",
        backend="claude-cli",
        model="sonnet",
        soul_document_blob_ref=store_key,
    )

    await preload_soul_caches_for_rows([row], blob)

    agent = agent_row_to_config(row)
    assert "Cold start Lyra" in agent.system_prompt
    assert "Bootstrap preload" in agent.system_prompt