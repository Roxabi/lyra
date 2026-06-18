"""ADR-089 E2E — OMP backend via OmpRpcDriver + mock NATS → Hub user error text.

Exercises the real OmpRpcDriver → OmpJobCodec → SimpleAgent → resolve_user_error()
chain. NATS is mocked (no omp worker container).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from factory.agents.simple_agent import SimpleAgent
from factory.core.agent import Agent
from factory.core.agent.agent_config import ModelConfig
from factory.core.messaging.message import Platform
from factory.llm.drivers.omp_rpc import OmpRpcDriver
from roxabi_contracts.jobs import JobResult
from roxabi_contracts.jobs.fixtures import ENV_BASE, sample_job_result_err
from tests.integration.test_adr089_error_resolution_e2e import (
    _hub_with_agent,
    _message_manager,
    _run_hub_until_processed,
)
from tests.integration.test_e2e_telegram_to_agent import _RecordingAdapter

pytestmark = [pytest.mark.omp_contract, pytest.mark.smoke]

_OMP_MODEL = ModelConfig(backend="omp-rpc", model="grok-4-fast")


def _make_omp_nc(
    job_result: JobResult | None = None,
    *,
    timeout: bool = False,
) -> AsyncMock:
    nc = AsyncMock()
    sub = AsyncMock()
    nc.subscribe.return_value = sub
    if timeout:
        sub.next_msg.side_effect = asyncio.TimeoutError
    elif job_result is not None:
        sub.next_msg.return_value = SimpleNamespace(
            data=job_result.model_dump_json().encode()
        )
    return nc


def _make_omp_agent(nc: AsyncMock) -> SimpleAgent:
    driver = OmpRpcDriver(nc, timeout_s=5.0)
    return SimpleAgent(
        Agent(
            name="lyra",
            system_prompt="You are Lyra.",
            memory_namespace="lyra",
            llm_config=_OMP_MODEL,
        ),
        driver,
        msg_manager=_message_manager(),
    )


class TestAdr089OmpBlockingErrorE2E:
    async def test_worker_crash_job_result_shows_generic(self) -> None:
        """JobResult worker.crash → generic (infra code, no message leak)."""
        result = JobResult.model_validate(sample_job_result_err)
        nc = _make_omp_nc(result)
        hub = _hub_with_agent(_make_omp_agent(nc))
        adapter = hub.adapter_registry[(Platform.TELEGRAM, "main")]
        assert isinstance(adapter, _RecordingAdapter)

        await _run_hub_until_processed(hub)

        assert len(adapter.sent) == 1
        text = adapter.sent[0].to_text()
        assert "scraper" not in text
        assert text == "Something went wrong. Please try again."

    async def test_llm_rate_limit_job_result_shows_template(self) -> None:
        """JobResult llm.rate_limit → messages.toml rate_limit template."""
        mm = _message_manager()
        result = JobResult.model_validate(
            {
                **ENV_BASE,
                "job_id": "job-omp-rl",
                "status": "error",
                "error": {
                    "code": "llm.rate_limit",
                    "message": "You've hit your weekly limit",
                    "retryable": True,
                },
            }
        )
        nc = _make_omp_nc(result)
        hub = _hub_with_agent(_make_omp_agent(nc))
        adapter = hub.adapter_registry[(Platform.TELEGRAM, "main")]
        assert isinstance(adapter, _RecordingAdapter)

        await _run_hub_until_processed(hub)

        assert len(adapter.sent) == 1
        assert adapter.sent[0].to_text() == mm.get("rate_limit")

    async def test_nats_timeout_shows_timeout_template(self) -> None:
        """NATS reply timeout → transport.timeout template."""
        mm = _message_manager()
        nc = _make_omp_nc(timeout=True)
        hub = _hub_with_agent(_make_omp_agent(nc))
        adapter = hub.adapter_registry[(Platform.TELEGRAM, "main")]
        assert isinstance(adapter, _RecordingAdapter)

        await _run_hub_until_processed(hub)

        assert len(adapter.sent) == 1
        assert adapter.sent[0].to_text() == mm.get("timeout")