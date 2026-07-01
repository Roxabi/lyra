"""Unit tests for dashboard jobs launch/steer RPC (#1773)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.bootstrap.factory.dashboard_jobs_rpc import (
    handle_jobs_cancel,
    handle_jobs_launch,
    handle_jobs_steer,
)
from roxabi_contracts.jobs.subjects import (
    JOB_CANCEL_STEER_TOKEN,
    jobs_steer,
    jobs_submit,
)


@pytest.fixture
def hub() -> MagicMock:
    h = MagicMock()
    agent = MagicMock()
    agent.config.system_prompt = "test soul"
    h.agent_registry = {"lyra": agent, "aryl": agent}
    row = MagicMock(backend="claude-cli", model="sonnet")
    store = MagicMock()
    store.get.return_value = row
    h._agent_store = store
    return h


@pytest.fixture
def nc() -> AsyncMock:
    client = AsyncMock()
    client.publish = AsyncMock()
    return client


class TestJobsLaunch:
    @pytest.mark.asyncio
    async def test_publishes_job_envelope(self, hub: MagicMock, nc: AsyncMock) -> None:
        result = await handle_jobs_launch(
            hub,
            nc,
            {"agent": "lyra", "prompt": "hello operator", "job_name": "omp"},
        )
        assert result["accepted"] is True
        assert result["job_id"]
        assert result["dispatch_subject"] == jobs_submit("omp")
        nc.publish.assert_awaited_once()
        subject, payload = nc.publish.await_args.args
        assert subject == "factory.jobs.omp"
        assert b"hello operator" in payload

    @pytest.mark.asyncio
    async def test_rejects_unknown_agent(self, hub: MagicMock, nc: AsyncMock) -> None:
        result = await handle_jobs_launch(
            hub,
            nc,
            {"agent": "ghost", "prompt": "nope", "job_name": "omp"},
        )
        assert result["accepted"] is False
        nc.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_publishes_claude_job_envelope(
        self, hub: MagicMock, nc: AsyncMock
    ) -> None:
        result = await handle_jobs_launch(
            hub,
            nc,
            {"agent": "lyra", "prompt": "claude run", "job_name": "claude"},
        )
        assert result["accepted"] is True
        assert result["dispatch_subject"] == jobs_submit("claude")
        subject, payload = nc.publish.await_args.args
        assert subject == "factory.jobs.claude"
        assert b"claude run" in payload
        assert b"claude-cli" in payload

    @pytest.mark.asyncio
    async def test_rejects_disallowed_job_name(
        self, hub: MagicMock, nc: AsyncMock
    ) -> None:
        result = await handle_jobs_launch(
            hub,
            nc,
            {"agent": "lyra", "prompt": "nope", "job_name": "vault.add"},
        )
        assert result["accepted"] is False
        nc.publish.assert_not_awaited()


class TestJobsSteer:
    @pytest.mark.asyncio
    async def test_publishes_steer_text(self, hub: MagicMock, nc: AsyncMock) -> None:
        result = await handle_jobs_steer(
            hub,
            nc,
            {"job_id": "abc123", "text": "change direction"},
        )
        assert result["accepted"] is True
        nc.publish.assert_awaited_once_with(
            jobs_steer("abc123"),
            b"change direction",
        )


class TestJobsCancel:
    @pytest.mark.asyncio
    async def test_publishes_cancel_token_and_closes_registry(
        self, hub: MagicMock, nc: AsyncMock
    ) -> None:
        coord = AsyncMock()
        hub._active_jobs_coord = coord
        result = await handle_jobs_cancel(hub, nc, {"job_id": "abc123"})
        assert result["accepted"] is True
        coord.close.assert_awaited_once_with("abc123")
        nc.publish.assert_awaited_once_with(
            jobs_steer("abc123"),
            JOB_CANCEL_STEER_TOKEN.encode(),
        )

    @pytest.mark.asyncio
    async def test_cancel_without_registry_still_publishes(
        self, hub: MagicMock, nc: AsyncMock
    ) -> None:
        hub._active_jobs_coord = None
        result = await handle_jobs_cancel(hub, nc, {"job_id": "abc123"})
        assert result["accepted"] is True
        nc.publish.assert_awaited_once_with(
            jobs_steer("abc123"),
            JOB_CANCEL_STEER_TOKEN.encode(),
        )