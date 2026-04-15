"""Tests for SupervisorctlManager (lyra.integrations.supervisor)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.integrations.base import ServiceControlFailed, ServiceManager
from lyra.integrations.supervisor import SupervisorctlManager


class TestSupervisorctlManagerProtocol:
    def test_implements_service_manager(self):
        assert isinstance(SupervisorctlManager(), ServiceManager)


class TestSupervisorctlManagerControl:
    @pytest.mark.asyncio
    async def test_status_all_returns_stdout(self):
        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"lyra RUNNING\n", b""))

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            result = await SupervisorctlManager().control("status", None)

        assert "lyra" in result

    @pytest.mark.asyncio
    async def test_status_all_does_not_append_service(self):
        calls: list[list] = []
        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"output", b""))

        async def fake_exec(*args, **kwargs):
            calls.append(list(args))
            return proc

        with patch("asyncio.create_subprocess_exec", new=fake_exec):
            await SupervisorctlManager().control("status", None)

        # script + action only, no service arg
        assert len(calls[0]) == 2
        assert "status" in calls[0]

    @pytest.mark.asyncio
    async def test_restart_service_calls_correct_args(self):
        calls: list[list] = []
        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"restarted", b""))

        async def fake_exec(*args, **kwargs):
            calls.append(list(args))
            return proc

        with patch("asyncio.create_subprocess_exec", new=fake_exec):
            await SupervisorctlManager().control("restart", "lyra")

        # Should contain the supervisorctl script path, action, and service
        cmd = calls[0]
        assert "restart" in cmd
        assert "lyra" in cmd

    @pytest.mark.asyncio
    async def test_file_not_found_raises(self):
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            with pytest.raises(ServiceControlFailed) as exc_info:
                await SupervisorctlManager().control("status", None)
            assert exc_info.value.reason == "not_available"

    @pytest.mark.asyncio
    async def test_nonzero_exit_raises(self):
        proc = MagicMock()
        proc.returncode = 1
        proc.communicate = AsyncMock(return_value=(b"", b"error"))

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            with pytest.raises(ServiceControlFailed) as exc_info:
                await SupervisorctlManager().control("restart", "lyra")
            assert exc_info.value.reason == "subprocess_error"

    @pytest.mark.asyncio
    async def test_timeout_raises(self):
        proc = MagicMock()
        proc.kill = MagicMock()
        proc.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            with patch(
                "lyra.integrations.supervisor.asyncio.wait_for",
                side_effect=asyncio.TimeoutError,
            ):
                with pytest.raises(ServiceControlFailed) as exc_info:
                    await SupervisorctlManager().control("status", None)
                assert exc_info.value.reason == "timeout"
