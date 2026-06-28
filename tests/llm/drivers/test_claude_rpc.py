"""Tests for ClaudeRpcDriver — JobEnvelope dispatch + pub/sub (phase 2)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.llm.drivers.claude_rpc import ClaudeRpcDriver
from roxabi_contracts.jobs import JobResult
from roxabi_contracts.jobs.fixtures import sample_job_result_ok
from roxabi_contracts.jobs.subjects import jobs_result, jobs_submit


@pytest.fixture()
def nc() -> AsyncMock:
    mock_nc = AsyncMock()
    sub = AsyncMock()
    mock_nc.subscribe.return_value = sub
    return mock_nc


@pytest.fixture()
def sub(nc: AsyncMock) -> AsyncMock:
    return nc.subscribe.return_value


@pytest.fixture()
def model_cfg() -> MagicMock:
    cfg = MagicMock()
    cfg.model_dump.return_value = {"backend": "claude-cli", "model": "sonnet"}
    return cfg


@pytest.fixture()
def driver(nc: AsyncMock) -> ClaudeRpcDriver:
    return ClaudeRpcDriver(nc, timeout_s=5.0)


class TestClaudeRpcDriver:
    def test_capabilities_declares_streaming(self, driver: ClaudeRpcDriver) -> None:
        assert driver.capabilities == {"streaming": True}

    @pytest.mark.asyncio
    async def test_complete_happy_path(
        self,
        driver: ClaudeRpcDriver,
        nc: AsyncMock,
        sub: AsyncMock,
        model_cfg: MagicMock,
    ) -> None:
        success = JobResult.model_validate(
            {**sample_job_result_ok, "data": {"result": "ok", "session_id": "sid"}}
        )
        sub.next_msg.return_value = SimpleNamespace(
            data=success.model_dump_json().encode()
        )

        res = await driver.complete(
            pool_id="p",
            text="hi",
            model_cfg=model_cfg,
            system_prompt="sys",
        )

        assert res.ok is True
        assert res.result == "ok"
        assert nc.publish.await_args.args[0] == jobs_submit("claude")
        envelope = json.loads(nc.publish.await_args.args[1])
        assert envelope["job_name"] == "claude"
        assert envelope["payload"]["prompt"] == "hi"
        assert nc.subscribe.await_args.args[0].endswith(".result")

    @pytest.mark.asyncio
    async def test_complete_subscribes_before_publish(
        self,
        driver: ClaudeRpcDriver,
        nc: AsyncMock,
        sub: AsyncMock,
        model_cfg: MagicMock,
    ) -> None:
        success = JobResult.model_validate({**sample_job_result_ok, "data": {"result": "x"}})
        sub.next_msg.return_value = SimpleNamespace(
            data=success.model_dump_json().encode()
        )
        await driver.complete("p", "hi", model_cfg, "sys")

        subscribe_idx = next(
            i for i, call in enumerate(nc.mock_calls) if call[0] == "subscribe"
        )
        publish_idx = next(
            i for i, call in enumerate(nc.mock_calls) if call[0] == "publish"
        )
        assert subscribe_idx < publish_idx