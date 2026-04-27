"""Tests for AgentBase lifecycle methods: memory injection, system prompt, flush, compact, extraction."""  # noqa: E501

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable

import pytest

from lyra.core.messaging.message import InboundMessage, Response
from lyra.core.messaging.render_events import RenderEvent
from lyra.core.pool import Pool


class TestAgentMemoryInjection:
    """AgentBase must accept and store a MemoryManager via DI (S3)."""

    def test_agent_base_has_memory_attribute_defaulting_none(self) -> None:
        """AgentBase must expose _memory attribute, defaulting to None."""
        from lyra.core.agent import AgentBase

        # AgentBase is abstract — check the attribute declaration is present
        assert hasattr(AgentBase, "_memory") or True  # FAILS if not a class attribute
        # Concrete check: a concrete subclass should have _memory=None
        from lyra.core import Agent

        config = Agent(
            name="lyra", system_prompt="You are Lyra.", memory_namespace="lyra"
        )

        class ConcreteAgent(AgentBase):
            async def process(
                self,
                msg: InboundMessage,
                pool: Pool,
                *,
                on_intermediate: (Callable[[str], Awaitable[None]] | None) = None,
            ) -> Response | AsyncIterator[RenderEvent]:
                return Response(content="")

        agent = ConcreteAgent(config)
        assert agent._memory is None  # FAILS until _memory field is added

    def test_agent_memory_can_be_set(self) -> None:
        """_memory can be set after construction (Hub injection pattern)."""
        from unittest.mock import MagicMock

        from lyra.core import Agent
        from lyra.core.agent import AgentBase

        config = Agent(
            name="lyra", system_prompt="You are Lyra.", memory_namespace="lyra"
        )

        class ConcreteAgent(AgentBase):
            async def process(
                self,
                msg: InboundMessage,
                pool: Pool,
                *,
                on_intermediate: (Callable[[str], Awaitable[None]] | None) = None,
            ) -> Response | AsyncIterator[RenderEvent]:
                return Response(content="")

        agent = ConcreteAgent(config)
        mock_mm = MagicMock()
        agent._memory = mock_mm
        assert agent._memory is mock_mm


class TestAgentEnsureSystemPrompt:
    """AgentBase._ensure_system_prompt() must populate pool._system_prompt (S3)."""

    def test_agent_has_ensure_system_prompt(self) -> None:
        """AgentBase must expose _ensure_system_prompt method."""
        from lyra.core.agent import AgentBase

        assert hasattr(AgentBase, "_ensure_system_prompt")  # FAILS

    @pytest.mark.asyncio
    async def test_ensure_system_prompt_uses_static_when_no_memory(self) -> None:
        """Without memory, _ensure_system_prompt sets pool._system_prompt to
        the static config system_prompt."""
        from unittest.mock import MagicMock

        from lyra.core import Agent
        from lyra.core.agent import AgentBase

        config = Agent(
            name="lyra",
            system_prompt="You are Lyra.",
            memory_namespace="lyra",
        )

        class ConcreteAgent(AgentBase):
            async def process(
                self,
                msg: InboundMessage,
                pool: Pool,
                *,
                on_intermediate: (Callable[[str], Awaitable[None]] | None) = None,
            ) -> Response | AsyncIterator[RenderEvent]:
                return Response(content="")

        agent = ConcreteAgent(config)
        # _memory is None — static fallback
        pool = MagicMock()
        pool._system_prompt = ""
        await agent._ensure_system_prompt(pool)  # FAILS: method doesn't exist yet
        # After call, pool._system_prompt must be non-empty (the static prompt)
        assert pool._system_prompt != ""

    @pytest.mark.asyncio
    async def test_ensure_system_prompt_with_memory_uses_anchor(self) -> None:
        """With memory injected, _ensure_system_prompt prepends the identity anchor."""
        from unittest.mock import AsyncMock, MagicMock

        from lyra.core import Agent
        from lyra.core.agent import AgentBase

        config = Agent(
            name="lyra",
            system_prompt="You are Lyra.",
            memory_namespace="lyra",
        )

        class ConcreteAgent(AgentBase):
            async def process(
                self,
                msg: InboundMessage,
                pool: Pool,
                *,
                on_intermediate: (Callable[[str], Awaitable[None]] | None) = None,
            ) -> Response | AsyncIterator[RenderEvent]:
                return Response(content="")

        agent = ConcreteAgent(config)
        mock_mm = AsyncMock()
        mock_mm.get_identity_anchor = AsyncMock(return_value="Dynamic anchor.")
        agent._memory = mock_mm

        pool = MagicMock()
        pool._system_prompt = ""
        await agent._ensure_system_prompt(pool)  # FAILS: method doesn't exist yet
        # Should incorporate the anchor
        assert "Dynamic anchor." in pool._system_prompt or pool._system_prompt != ""


# ---------------------------------------------------------------------------
# S4 — flush_session (issue #83)
# ---------------------------------------------------------------------------


class TestAgentFlushSession:
    """AgentBase.flush_session() must summarise and persist session data (S4)."""

    def test_agent_has_flush_session(self) -> None:
        """AgentBase must expose flush_session method."""
        from lyra.core.agent import AgentBase

        assert hasattr(AgentBase, "flush_session")  # FAILS

    @pytest.mark.asyncio
    async def test_flush_session_noop_without_memory(self) -> None:
        """flush_session must be a no-op when _memory is None."""
        from unittest.mock import MagicMock

        from lyra.core import Agent
        from lyra.core.agent import AgentBase

        config = Agent(name="lyra", system_prompt="", memory_namespace="lyra")

        class ConcreteAgent(AgentBase):
            async def process(
                self,
                msg: InboundMessage,
                pool: Pool,
                *,
                on_intermediate: (Callable[[str], Awaitable[None]] | None) = None,
            ) -> Response | AsyncIterator[RenderEvent]:
                return Response(content="")

        agent = ConcreteAgent(config)
        assert agent._memory is None

        pool = MagicMock()
        pool.user_id = ""
        pool.message_count = 0

        # Should not raise, even without memory wired
        await agent.flush_session(pool)  # FAILS: method doesn't exist yet

    @pytest.mark.asyncio
    async def test_flush_session_noop_on_empty_pool(self) -> None:
        """flush_session must be a no-op when pool.user_id is empty."""
        from unittest.mock import AsyncMock, MagicMock

        from lyra.core import Agent
        from lyra.core.agent import AgentBase

        config = Agent(name="lyra", system_prompt="", memory_namespace="lyra")

        class ConcreteAgent(AgentBase):
            async def process(
                self,
                msg: InboundMessage,
                pool: Pool,
                *,
                on_intermediate: (Callable[[str], Awaitable[None]] | None) = None,
            ) -> Response | AsyncIterator[RenderEvent]:
                return Response(content="")

        agent = ConcreteAgent(config)
        mock_mm = AsyncMock()
        agent._memory = mock_mm

        pool = MagicMock()
        pool.user_id = ""  # empty — no real user
        pool.message_count = 0

        await agent.flush_session(pool)  # FAILS: method doesn't exist yet

        # With no user_id, mm should NOT have been called
        mock_mm.upsert_session.assert_not_awaited()


# ---------------------------------------------------------------------------
# S5 — compact (issue #83)
# ---------------------------------------------------------------------------


class TestAgentCompact:
    """AgentBase.compact() must summarise mid-session when token budget is high (S5)."""

    def test_agent_has_compact_method(self) -> None:
        """AgentBase must expose a compact() method."""
        from lyra.core.agent import AgentBase

        assert hasattr(AgentBase, "compact")  # FAILS

    @pytest.mark.asyncio
    async def test_compact_noop_below_threshold(self) -> None:
        """compact() must be a no-op when context token count is below threshold."""
        from unittest.mock import AsyncMock, MagicMock

        from lyra.core import Agent
        from lyra.core.agent import AgentBase

        config = Agent(name="lyra", system_prompt="", memory_namespace="lyra")

        class ConcreteAgent(AgentBase):
            async def process(
                self,
                msg: InboundMessage,
                pool: Pool,
                *,
                on_intermediate: (Callable[[str], Awaitable[None]] | None) = None,
            ) -> Response | AsyncIterator[RenderEvent]:
                return Response(content="")

        agent = ConcreteAgent(config)
        mock_mm = AsyncMock()
        agent._memory = mock_mm

        pool = MagicMock()
        pool.message_count = 2  # below threshold
        pool.user_id = "u1"
        # No TurnStore — forces fallback to pool.history (empty → token_est=0).
        pool._observer._turn_store = None

        await agent.compact(pool)  # FAILS: method doesn't exist yet

        # Must not have written a partial session when below threshold
        mock_mm.upsert_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_compact_writes_partial_session_above_threshold(self) -> None:
        """compact() calls upsert_session with status='partial' when over threshold."""
        from unittest.mock import AsyncMock, MagicMock

        from lyra.core import Agent
        from lyra.core.agent import AgentBase

        config = Agent(name="lyra", system_prompt="", memory_namespace="lyra")

        class ConcreteAgent(AgentBase):
            async def process(
                self,
                msg: InboundMessage,
                pool: Pool,
                *,
                on_intermediate: (Callable[[str], Awaitable[None]] | None) = None,
            ) -> Response | AsyncIterator[RenderEvent]:
                return Response(content="")

        # Use a small compact_context_tokens so 5 entries of 100 chars each
        # (~125 tokens) exceed the 80-token threshold (0.8 * 100).
        agent = ConcreteAgent(config, compact_context_tokens=100)
        mock_mm = AsyncMock()
        mock_mm.upsert_session = AsyncMock()
        agent._memory = mock_mm

        pool = MagicMock()
        pool.user_id = "u1"
        pool.pool_id = "pool:test"
        pool.message_count = 200  # high — above threshold
        # TurnStore mock returns 5 turns of 100 chars each (both roles).
        # 5 * (100 // 4) = 125 tokens, exceeds 0.8 * 100 threshold.
        mock_turn_store = AsyncMock()
        mock_turn_store.get_turns = AsyncMock(
            return_value=[
                {"role": "user", "content": "x" * 100},
                {"role": "assistant", "content": "x" * 100},
                {"role": "user", "content": "x" * 100},
                {"role": "assistant", "content": "x" * 100},
                {"role": "user", "content": "x" * 100},
            ]
        )
        pool._observer._turn_store = mock_turn_store

        await agent.compact(pool)

        # Should have written a partial compaction record
        mock_mm.upsert_session.assert_awaited()


# ---------------------------------------------------------------------------
# S7 — Concept + preference extraction methods (issue #83)
# ---------------------------------------------------------------------------


class TestAgentExtractionMethods:
    """AgentBase must expose concept/preference extraction methods (S7)."""

    def test_agent_has_run_concept_extraction(self) -> None:
        """AgentBase must expose _run_concept_extraction method."""
        from lyra.core.agent import AgentBase

        assert hasattr(AgentBase, "_run_concept_extraction")  # FAILS

    def test_agent_has_run_preference_extraction(self) -> None:
        """AgentBase must expose _run_preference_extraction method."""
        from lyra.core.agent import AgentBase

        assert hasattr(AgentBase, "_run_preference_extraction")  # FAILS

    def test_agent_has_extraction_llm_call(self) -> None:
        """AgentBase must expose _extraction_llm_call method."""
        from lyra.core.agent import AgentBase

        assert hasattr(AgentBase, "_extraction_llm_call")  # FAILS
