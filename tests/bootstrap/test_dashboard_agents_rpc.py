"""Unit tests for dashboard agent + soul NATS RPC handlers."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.bootstrap.factory.dashboard_agents_rpc import (
    handle_agents_get,
    handle_agents_list,
    handle_agents_patch,
    handle_agents_soul_get,
    handle_agents_soul_preview,
    handle_agents_soul_put,
)
from factory.core.agent.agent_models import AgentRow
from factory.core.agent.soul_cache import get_soul_document_cache
from roxabi_contracts.blob_ref import BlobRef

_NC = MagicMock()

_SOUL_MD = """## Identity
Lyra test
## Personality
Warm
## Values
Safe
## Expertise
Code
## Guidelines
Direct
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


def _hub_with_store(row: AgentRow | None = None) -> MagicMock:
    hub = MagicMock()
    store = MagicMock()
    store.get_all.return_value = [row] if row else []
    store.get.return_value = row
    store.upsert = AsyncMock()
    hub._agent_store = store
    hub._blob_store = _MemoryBlobStore()
    return hub


@pytest.mark.asyncio
async def test_handle_agents_list_returns_summaries() -> None:
    row = AgentRow(name="lyra", backend="claude-cli", model="sonnet")
    hub = _hub_with_store(row)
    out = await handle_agents_list(hub, _NC, {})
    assert out["agents"][0]["name"] == "lyra"
    assert out["agents"][0]["has_soul"] is False


@pytest.mark.asyncio
async def test_handle_agents_get_returns_config() -> None:
    row = AgentRow(
        name="lyra",
        backend="omp-rpc",
        model="grok-4-fast",
        soul_document_blob_ref="sha256:abc",
        soul_document_bytes=42,
    )
    hub = _hub_with_store(row)
    out = await handle_agents_get(hub, _NC, {"name": "lyra"})
    assert out["backend"] == "omp-rpc"
    assert out["soul_document_blob_ref"] == "sha256:abc"


@pytest.mark.asyncio
async def test_handle_agents_soul_put_drives_put_soul_document() -> None:
    get_soul_document_cache().invalidate("lyra")
    row = AgentRow(name="lyra", backend="claude-cli", model="sonnet")
    hub = _hub_with_store(row)

    async def _upsert(updated: AgentRow) -> None:
        hub._agent_store.get.return_value = updated

    hub._agent_store.upsert = AsyncMock(side_effect=_upsert)

    out = await handle_agents_soul_put(
        hub,
        _NC,
        {"name": "lyra", "markdown": _SOUL_MD},
    )
    assert "error" not in out
    assert out["sections"]["Identity"] == "Lyra test"
    hub._agent_store.upsert.assert_awaited_once()
    cached = get_soul_document_cache().get("lyra", out["soul_document_blob_ref"])
    assert cached is not None
    assert "Lyra test" in cached.composed_prompt


@pytest.mark.asyncio
async def test_handle_agents_soul_get_reads_cache_after_put() -> None:
    get_soul_document_cache().invalidate("lyra")
    row = AgentRow(name="lyra", backend="claude-cli", model="sonnet")
    hub = _hub_with_store(row)

    async def _upsert(updated: AgentRow) -> None:
        hub._agent_store.get.return_value = updated

    hub._agent_store.upsert = AsyncMock(side_effect=_upsert)

    put_out = await handle_agents_soul_put(
        hub, _NC, {"name": "lyra", "markdown": _SOUL_MD}
    )
    out = await handle_agents_soul_get(hub, _NC, {"name": "lyra"})
    assert put_out["soul_document_blob_ref"]
    assert out["sections"]["Identity"] == "Lyra test"
    assert out["sections"]["Guidelines"] == "Direct"


@pytest.mark.asyncio
async def test_handle_agents_soul_get_falls_back_to_persona_json() -> None:
    persona = {
        "identity": {"display_name": "Legacy Lyra", "goal": "Help operators"},
        "personality": {"traits": ["calm"], "tone": "direct"},
        "expertise": {"areas": ["Python"], "instructions": ["Be concise"]},
    }
    row = AgentRow(
        name="lyra",
        backend="claude-cli",
        model="sonnet",
        persona_json=json.dumps(persona),
    )
    hub = _hub_with_store(row)
    hub._blob_store = None

    out = await handle_agents_soul_get(hub, _NC, {"name": "lyra"})

    assert "Legacy Lyra" in out["sections"]["Identity"]
    assert "calm" in out["sections"]["Personality"]
    assert out["sections"]["Guidelines"] == "- Be concise"
    assert out["soul_document_blob_ref"] is None


@pytest.mark.asyncio
async def test_handle_agents_soul_preview_composes_via_hub() -> None:
    hub = MagicMock()
    out = await handle_agents_soul_preview(
        hub,
        _NC,
        {"sections": {"Identity": "Preview agent"}},
    )
    assert "Preview agent" in out["composed"]
    assert out["truncated"] is False


@pytest.mark.asyncio
async def test_handle_agents_patch_updates_scalars() -> None:
    row = AgentRow(
        name="lyra",
        backend="claude-cli",
        model="sonnet",
        soul_meta_json=json.dumps(
            {"schema_version": 1, "header": {"display_name": "", "tagline": ""}}
        ),
    )
    hub = _hub_with_store(row)

    async def _upsert(updated: AgentRow) -> None:
        hub._agent_store.get.return_value = updated

    hub._agent_store.upsert = AsyncMock(side_effect=_upsert)

    out = await handle_agents_patch(
        hub,
        _NC,
        {
            "name": "lyra",
            "patch": {
                "backend": "omp-rpc",
                "model": "grok-4-fast",
                "display_name": "Lyra",
            },
        },
    )
    assert out["backend"] == "omp-rpc"
    assert out["model"] == "grok-4-fast"
    assert out["soul_meta_json"]["header"]["display_name"] == "Lyra"