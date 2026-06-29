"""Cross-harness soul parity — hub composes once, harnesses receive same opaque str."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.adapters.omp.omp_pool import OmpPool
from factory.core.agent.agent_db_loader import agent_row_to_config
from factory.core.agent.agent_models import AgentRow
from factory.core.agent.soul_cache import get_soul_document_cache
from factory.core.persona import compose_soul_document_from_markdown
from factory.infrastructure.soul.soul_ops import warm_soul_cache

_SOUL_MD = """## Identity
Parity Lyra
## Personality
Warm
## Values
Safe
## Expertise
Code
## Guidelines
Direct
"""


class TestSoulHarnessParity:
    def setup_method(self) -> None:
        get_soul_document_cache().invalidate("lyra")

    def test_clipool_path_uses_hub_composed_string(self) -> None:
        composed = warm_soul_cache("lyra", blob_ref="sha256:parity", markdown=_SOUL_MD)
        row = AgentRow(
            name="lyra",
            backend="claude-cli",
            model="sonnet",
            soul_document_blob_ref="sha256:parity",
        )
        agent = agent_row_to_config(row)
        assert agent.system_prompt == composed
        assert "Parity Lyra" in agent.system_prompt

    @pytest.mark.asyncio
    async def test_omp_acquire_receives_identical_composed_string(self) -> None:
        composed = compose_soul_document_from_markdown(_SOUL_MD)
        client = MagicMock()
        client.new_session = MagicMock()
        state = MagicMock(session_file="/tmp/s.jsonl")
        client.get_state = MagicMock(return_value=state)
        client.set_system_prompt = MagicMock()
        client.start = MagicMock()
        client.stop = MagicMock()
        bridge = MagicMock()
        bridge.attach = AsyncMock()

        pool = OmpPool(omp_bin=MagicMock())
        pool._nc = MagicMock()
        pool._loop = __import__("asyncio").get_event_loop()

        async def _fake_start() -> object:
            from factory.adapters.omp.omp_pool import _PoolWorker

            w = _PoolWorker(client=client, bridge=bridge)
            pool._all.append(w)
            return w

        pool._start_worker = _fake_start  # type: ignore[method-assign]
        await pool.register(MagicMock())

        worker = await pool.acquire(None, system_prompt=composed)
        assert worker.system_prompt == composed
        client.set_system_prompt.assert_called_once_with(composed)

    @pytest.mark.asyncio
    async def test_persona_edit_triggers_new_omp_session_with_new_soul(self) -> None:
        client = MagicMock()
        client.new_session = MagicMock()
        state = MagicMock(session_file="/tmp/s.jsonl")
        client.get_state = MagicMock(return_value=state)
        client.set_system_prompt = MagicMock()
        client.start = MagicMock()
        client.stop = MagicMock()
        bridge = MagicMock()
        bridge.attach = AsyncMock()

        pool = OmpPool(omp_bin=MagicMock())
        pool._nc = MagicMock()
        pool._loop = __import__("asyncio").get_event_loop()

        async def _fake_start() -> object:
            from factory.adapters.omp.omp_pool import _PoolWorker

            w = _PoolWorker(client=client, bridge=bridge)
            pool._all.append(w)
            return w

        pool._start_worker = _fake_start  # type: ignore[method-assign]
        await pool.register(MagicMock())

        soul_v1 = compose_soul_document_from_markdown("## Identity\nVersion one\n")
        await pool.acquire(None, system_prompt=soul_v1)
        pool.release(pool._all[0])

        soul_v2 = compose_soul_document_from_markdown("## Identity\nVersion two\n")
        client.new_session.reset_mock()
        client.set_system_prompt.reset_mock()
        worker2 = await pool.acquire(None, system_prompt=soul_v2)
        assert worker2.system_prompt == soul_v2
        client.new_session.assert_called_once()
        client.set_system_prompt.assert_called_once_with(soul_v2)