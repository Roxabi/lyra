"""Cross-harness soul parity — hub composes once, harnesses receive same opaque str."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.adapters.omp.omp_pool import OmpPool, _PoolWorker
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
        start_prompts: list[str] = []

        client = MagicMock()
        client.new_session = MagicMock()
        state = MagicMock(session_file="/tmp/s.jsonl")
        client.get_state = MagicMock(return_value=state)
        client.start = MagicMock()
        client.stop = MagicMock()
        bridge = MagicMock()
        bridge.attach = AsyncMock()

        pool = OmpPool(omp_bin=MagicMock())
        pool._nc = MagicMock()
        pool._loop = __import__("asyncio").get_event_loop()

        async def _fake_start(*, system_prompt: str = "") -> _PoolWorker:
            start_prompts.append(system_prompt)
            w = _PoolWorker(client=client, bridge=bridge, system_prompt=system_prompt)
            pool._all.append(w)
            return w

        pool._start_worker = _fake_start  # type: ignore[method-assign]
        await pool.register(MagicMock())

        worker = await pool.acquire(None, system_prompt=composed)
        assert worker.system_prompt == composed
        assert start_prompts == [composed]

    @pytest.mark.asyncio
    async def test_persona_edit_triggers_new_omp_session_with_new_soul(self) -> None:
        start_prompts: list[str] = []
        clients: list[MagicMock] = []

        def _make_client() -> MagicMock:
            client = MagicMock()
            client.new_session = MagicMock()
            state = MagicMock(session_file="/tmp/s.jsonl")
            client.get_state = MagicMock(return_value=state)
            client.start = MagicMock()
            client.stop = MagicMock()
            clients.append(client)
            return client

        bridge = MagicMock()
        bridge.attach = AsyncMock()

        pool = OmpPool(omp_bin=MagicMock())
        pool._nc = MagicMock()
        pool._loop = __import__("asyncio").get_event_loop()

        async def _fake_start(*, system_prompt: str = "") -> _PoolWorker:
            start_prompts.append(system_prompt)
            w = _PoolWorker(
                client=_make_client(), bridge=bridge, system_prompt=system_prompt
            )
            pool._all.append(w)
            return w

        pool._start_worker = _fake_start  # type: ignore[method-assign]
        await pool.register(MagicMock())

        soul_v1 = compose_soul_document_from_markdown("## Identity\nVersion one\n")
        w1 = await pool.acquire(None, system_prompt=soul_v1)
        pool.release(w1)

        soul_v2 = compose_soul_document_from_markdown("## Identity\nVersion two\n")
        worker2 = await pool.acquire(None, system_prompt=soul_v2)
        assert worker2.system_prompt == soul_v2
        assert start_prompts == [soul_v1, soul_v2]
        clients[0].stop.assert_called_once()
        clients[1].new_session.assert_called_once()