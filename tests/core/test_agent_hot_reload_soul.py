"""Hot-reload preloads soul cache before agent_row_to_config."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from factory.core.agent.agent import AgentBase
from factory.core.agent.agent_config import Agent, ModelConfig
from factory.core.agent.agent_models import AgentRow
from factory.core.agent.soul_cache import get_soul_document_cache
from factory.infrastructure.soul.soul_ops import preload_soul_caches_for_rows
from roxabi_contracts.blob_ref import BlobRef

_SOUL_MD = """## Identity
Hot reload Lyra
## Personality
Calm
## Values
Honest
## Expertise
Python
## Guidelines
Reload path
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
        return None


class _ConcreteAgent(AgentBase):
    async def process(self, msg, pool):  # noqa: ARG002
        raise NotImplementedError


@pytest.mark.asyncio
async def test_hot_reload_preloads_soul_before_compose() -> None:
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
        updated_at="2026-06-30T00:00:01",
    )

    config = Agent(
        name="lyra",
        system_prompt="",
        memory_namespace="lyra",
        llm_config=ModelConfig(backend="claude-cli", model="sonnet"),
    )
    mock_store = MagicMock()
    mock_store.get.return_value = row
    agent = _ConcreteAgent(config, agent_store=mock_store)

    async def _preload(row: AgentRow) -> None:
        await preload_soul_caches_for_rows([row], blob)

    agent._preload_soul_for_row = _preload

    await agent._maybe_reload_config()

    assert "Hot reload Lyra" in agent.config.system_prompt
    assert "Reload path" in agent.config.system_prompt
