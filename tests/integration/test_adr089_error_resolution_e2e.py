"""ADR-089 E2E — Telegram → Hub → SimpleAgent → resolved user error text.

In-process smoke: mocked LlmProvider, real Hub routing, real MessageManager
and resolve_user_error() wiring. No NATS / no live CLI.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.agents.simple_agent import SimpleAgent
from factory.core.agent import Agent
from factory.core.agent.agent_config import ModelConfig
from factory.core.messaging.events import ResultLlmEvent
from factory.core.messaging.message import Platform
from factory.llm.base import LlmResult
from roxabi_contracts.errors import WorkerError
from tests.integration.adr089_helpers import (
    DrainingStreamingAdapter,
    hub_with_agent,
    message_manager,
    run_hub_until_processed,
)
from tests.integration.test_e2e_telegram_to_agent import _RecordingAdapter


@pytest.mark.smoke
class TestAdr089BlockingErrorE2E:
    async def test_rate_limit_template_reaches_telegram_adapter(self) -> None:
        mm = message_manager()
        provider = MagicMock()
        provider.complete = AsyncMock(
            return_value=LlmResult(
                error="You've hit your weekly limit",
                worker_error=WorkerError(
                    code="llm.rate_limit",
                    message="You've hit your weekly limit",
                    retryable=True,
                ),
            )
        )
        provider.is_alive = MagicMock(return_value=True)
        agent = SimpleAgent(
            Agent(
                name="lyra",
                system_prompt="You are Lyra.",
                memory_namespace="lyra",
                llm_config=ModelConfig(streaming=False),
            ),
            provider,
            msg_manager=mm,
        )
        hub = hub_with_agent(agent)
        adapter = hub.adapter_registry[(Platform.TELEGRAM, "main")]
        assert isinstance(adapter, _RecordingAdapter)

        await run_hub_until_processed(hub)

        assert len(adapter.sent) == 1
        assert adapter.sent[0].to_text() == mm.get("rate_limit")

    async def test_cli_parse_passthrough_reaches_telegram_adapter(self) -> None:
        curated = "You've hit your weekly limit · resets 6pm (UTC)"
        provider = MagicMock()
        provider.complete = AsyncMock(
            return_value=LlmResult(
                error=curated,
                worker_error=WorkerError(
                    code="cli.parse",
                    message=curated,
                    retryable=False,
                ),
            )
        )
        provider.is_alive = MagicMock(return_value=True)
        agent = SimpleAgent(
            Agent(
                name="lyra",
                system_prompt="You are Lyra.",
                memory_namespace="lyra",
                llm_config=ModelConfig(streaming=False),
            ),
            provider,
            msg_manager=message_manager(),
        )
        hub = hub_with_agent(agent)
        adapter = hub.adapter_registry[(Platform.TELEGRAM, "main")]
        assert isinstance(adapter, _RecordingAdapter)

        await run_hub_until_processed(hub)

        assert len(adapter.sent) == 1
        assert "weekly limit" in adapter.sent[0].to_text()

    async def test_flat_error_without_worker_error_is_generic(self) -> None:
        provider = MagicMock()
        provider.complete = AsyncMock(
            return_value=LlmResult(error="internal scraper stack trace")
        )
        provider.is_alive = MagicMock(return_value=True)
        agent = SimpleAgent(
            Agent(
                name="lyra",
                system_prompt="You are Lyra.",
                memory_namespace="lyra",
                llm_config=ModelConfig(streaming=False),
            ),
            provider,
            msg_manager=message_manager(),
        )
        hub = hub_with_agent(agent)
        adapter = hub.adapter_registry[(Platform.TELEGRAM, "main")]
        assert isinstance(adapter, _RecordingAdapter)

        await run_hub_until_processed(hub)

        assert len(adapter.sent) == 1
        text = adapter.sent[0].to_text()
        assert "scraper" not in text
        assert "stack trace" not in text
        assert text == "Something went wrong. Please try again."


@pytest.mark.smoke
class TestAdr089StreamingSoftErrorE2E:
    async def test_rate_limit_soft_error_reaches_streaming_adapter(self) -> None:
        mm = message_manager()
        expected = mm.get("rate_limit")

        async def _error_stream() -> AsyncIterator[ResultLlmEvent]:
            yield ResultLlmEvent(
                is_error=True,
                duration_ms=10,
                error_text="You've hit your weekly limit",
                worker_error=WorkerError(
                    code="llm.rate_limit",
                    message="You've hit your weekly limit",
                    retryable=True,
                ),
            )

        provider = MagicMock()
        provider.stream = MagicMock(return_value=_error_stream())
        provider.is_alive = MagicMock(return_value=True)

        agent = SimpleAgent(
            Agent(
                name="lyra",
                system_prompt="You are Lyra.",
                memory_namespace="lyra",
                llm_config=ModelConfig(streaming=True),
            ),
            provider,
            msg_manager=mm,
        )
        hub = hub_with_agent(agent)
        adapter = hub.adapter_registry[(Platform.TELEGRAM, "main")]
        assert isinstance(adapter, DrainingStreamingAdapter)

        await run_hub_until_processed(hub)

        assert len(adapter.streamed) == 1
        assert adapter.run_error_messages == [expected]
