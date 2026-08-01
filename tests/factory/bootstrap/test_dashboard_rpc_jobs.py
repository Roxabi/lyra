"""Unit tests for dashboard jobs launch/steer RPC (#1773)."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.bootstrap.factory.dashboard_jobs_rpc import (
    handle_jobs_cancel,
    handle_jobs_launch,
    handle_jobs_steer,
)
from factory.core.auth.control_plane import ControlPlanePrincipal, GlobalRole
from factory.core.auth.control_plane_wire import (
    clear_request_principal,
    set_request_principal,
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
    h._control_plane = None
    return h


@pytest.fixture
def nc() -> AsyncMock:
    client = AsyncMock()
    client.publish = AsyncMock()
    return client


@pytest.fixture(autouse=True)
def admin_principal():
    p = ControlPlanePrincipal(
        user_id="rx:user:admin",
        roles=frozenset({GlobalRole.ADMIN.value}),
        org_ids=frozenset(),
        active_org_id=None,
        via="session",
    )
    set_request_principal(p)
    yield p
    clear_request_principal()


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
    async def test_launch_opens_registry_with_envelope_job_id(
        self, hub: MagicMock, nc: AsyncMock
    ) -> None:
        """Dashboard dispatch registers so Jobs view + ResultClose see it (#2142)."""
        coord = AsyncMock()
        hub._active_jobs_coord = coord
        result = await handle_jobs_launch(
            hub,
            nc,
            {"agent": "lyra", "prompt": "track me", "job_name": "omp"},
        )
        assert result["accepted"] is True
        job_id = result["job_id"]
        coord.open.assert_awaited_once()
        entry = coord.open.await_args.args[0]
        assert entry.job_id == job_id
        assert entry.concurrency_mode == "steer"  # omp-rpc backend
        assert entry.steer_subject == jobs_steer(job_id)
        assert entry.pool_id  # synthetic web:smoke:agent:lyra

    @pytest.mark.asyncio
    async def test_launch_registry_open_failure_still_accepts(
        self, hub: MagicMock, nc: AsyncMock
    ) -> None:
        coord = AsyncMock()
        coord.open = AsyncMock(side_effect=RuntimeError("kv down"))
        hub._active_jobs_coord = coord
        result = await handle_jobs_launch(
            hub,
            nc,
            {"agent": "lyra", "prompt": "still ok", "job_name": "omp"},
        )
        assert result["accepted"] is True
        nc.publish.assert_awaited()

    @pytest.mark.asyncio
    async def test_launch_claude_registers_queue_mode(
        self, hub: MagicMock, nc: AsyncMock
    ) -> None:
        coord = AsyncMock()
        hub._active_jobs_coord = coord
        result = await handle_jobs_launch(
            hub,
            nc,
            {"agent": "lyra", "prompt": "cli job", "job_name": "claude"},
        )
        assert result["accepted"] is True
        entry = coord.open.await_args.args[0]
        assert entry.concurrency_mode == "queue"

    @pytest.mark.no_default_trace
    @pytest.mark.asyncio
    async def test_launch_mints_trace_without_ambient_context(
        self, hub: MagicMock, nc: AsyncMock
    ) -> None:
        # Prod repro: the dashboard RPC entry point has NO ambient TraceContext
        # (unlike hub work-path codecs). handle_jobs_launch must mint its own
        # root trace, not raise — regression from #2069 that shipped a launch 502.
        result = await handle_jobs_launch(
            hub,
            nc,
            {"agent": "lyra", "prompt": "no ambient trace", "job_name": "omp"},
        )
        assert result["accepted"] is True
        assert result["job_id"]
        nc.publish.assert_awaited_once()
        _, payload = nc.publish.await_args.args
        data = json.loads(payload)
        uuid.UUID(data["trace_id"])  # a well-formed root trace was minted

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


def _member(
    *,
    user_id: str = "rx:user:a",
    orgs: frozenset[str] | None = None,
) -> ControlPlanePrincipal:
    return ControlPlanePrincipal(
        user_id=user_id,
        roles=frozenset({GlobalRole.MEMBER.value}),
        org_ids=orgs or frozenset(),
        active_org_id=None,
        via="session",
    )


def _cp_with_job_meta(launched_by: str | None, org_id: str | None) -> MagicMock:
    cp = MagicMock()
    cp.get_job_meta = AsyncMock(return_value=(launched_by, org_id))
    return cp


class TestJobsAuthzDeny:
    """Non-admin ownership/org checks (#2316 R2)."""

    @pytest.mark.asyncio
    async def test_steer_without_principal_unauthorized(
        self, hub: MagicMock, nc: AsyncMock
    ) -> None:
        clear_request_principal()
        result = await handle_jobs_steer(hub, nc, {"job_id": "j1", "text": "x"})
        assert result["error"] == "unauthorized"
        nc.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_steer_outsider_forbidden(
        self, hub: MagicMock, nc: AsyncMock
    ) -> None:
        set_request_principal(_member(user_id="rx:user:a"))
        hub._control_plane = _cp_with_job_meta("rx:user:other", None)
        result = await handle_jobs_steer(hub, nc, {"job_id": "j1", "text": "hijack"})
        assert result.get("error") == "forbidden"
        assert result["accepted"] is False
        nc.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_steer_no_meta_forbidden_for_member(
        self, hub: MagicMock, nc: AsyncMock
    ) -> None:
        set_request_principal(_member())
        hub._control_plane = _cp_with_job_meta(None, None)
        result = await handle_jobs_steer(hub, nc, {"job_id": "orphan", "text": "x"})
        assert result.get("error") == "forbidden"
        nc.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_steer_owner_allowed(self, hub: MagicMock, nc: AsyncMock) -> None:
        set_request_principal(_member(user_id="rx:user:a"))
        hub._control_plane = _cp_with_job_meta("rx:user:a", None)
        result = await handle_jobs_steer(hub, nc, {"job_id": "j1", "text": "nudge"})
        assert result["accepted"] is True
        nc.publish.assert_awaited_once_with(jobs_steer("j1"), b"nudge")

    @pytest.mark.asyncio
    async def test_steer_org_peer_allowed(self, hub: MagicMock, nc: AsyncMock) -> None:
        set_request_principal(_member(user_id="rx:user:a", orgs=frozenset({"org:1"})))
        hub._control_plane = _cp_with_job_meta("rx:user:b", "org:1")
        result = await handle_jobs_steer(hub, nc, {"job_id": "j1", "text": "peer"})
        assert result["accepted"] is True
        nc.publish.assert_awaited_once_with(jobs_steer("j1"), b"peer")

    @pytest.mark.asyncio
    async def test_cancel_outsider_forbidden(
        self, hub: MagicMock, nc: AsyncMock
    ) -> None:
        set_request_principal(_member(user_id="rx:user:a"))
        hub._control_plane = _cp_with_job_meta("rx:user:other", None)
        result = await handle_jobs_cancel(hub, nc, {"job_id": "j1"})
        assert result.get("error") == "forbidden"
        assert result["accepted"] is False
        nc.publish.assert_not_awaited()
