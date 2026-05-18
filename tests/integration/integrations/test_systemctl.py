"""Tests for SystemctlManager (lyra.integrations.systemctl)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.integrations.base import ServiceControlFailed, ServiceManager
from lyra.integrations.systemctl import SystemctlManager


class TestSystemctlManagerProtocol:
    def test_implements_service_manager(self):
        assert isinstance(SystemctlManager(), ServiceManager)


class TestSystemctlManagerControl:
    @pytest.mark.asyncio
    async def test_status_all_invokes_every_unit(self):
        calls: list[list] = []
        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"all good", b""))

        async def fake_exec(*args, **kwargs):
            calls.append(list(args))
            return proc

        with patch("asyncio.create_subprocess_exec", new=fake_exec):
            await SystemctlManager().control("status", None)

        argv = calls[0]
        assert argv[:3] == ["systemctl", "--user", "status"]
        assert "lyra-hub.service" in argv
        assert "lyra-nats.service" in argv
        assert "voicecli-stt.service" in argv

    @pytest.mark.asyncio
    async def test_restart_lyra_targets_all_lyra_units(self):
        calls: list[list] = []
        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"restarted", b""))

        async def fake_exec(*args, **kwargs):
            calls.append(list(args))
            return proc

        with patch("asyncio.create_subprocess_exec", new=fake_exec):
            await SystemctlManager().control("restart", "lyra")

        argv = calls[0]
        assert argv[:3] == ["systemctl", "--user", "restart"]
        assert "lyra-hub.service" in argv
        assert "voicecli-stt.service" not in argv

    @pytest.mark.asyncio
    async def test_unknown_service_raises(self):
        with pytest.raises(ServiceControlFailed) as excinfo:
            await SystemctlManager().control("restart", "bogus")
        assert excinfo.value.reason == "subprocess_error"

    @pytest.mark.asyncio
    async def test_unknown_action_raises(self):
        with pytest.raises(ServiceControlFailed) as excinfo:
            await SystemctlManager().control("nuke", "lyra")
        assert excinfo.value.reason == "subprocess_error"

    @pytest.mark.asyncio
    async def test_systemctl_missing_raises_not_available(self):
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError()):
            with pytest.raises(ServiceControlFailed) as excinfo:
                await SystemctlManager().control("status", "lyra")

        assert excinfo.value.reason == "not_available"

    @pytest.mark.asyncio
    async def test_status_tolerates_rc3_inactive(self):
        """rc=3 means units exist but are inactive — valid status output, not error."""
        proc = MagicMock()
        proc.returncode = 3  # systemctl status exits 3 when units inactive
        proc.communicate = AsyncMock(return_value=(b"some inactive", b""))

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            result = await SystemctlManager().control("status", "lyra")

        assert "inactive" in result

    @pytest.mark.asyncio
    async def test_status_raises_on_rc4(self):
        """rc=4 means no such unit — raises ServiceControlFailed('not_available')."""
        proc = MagicMock()
        proc.returncode = 4
        proc.communicate = AsyncMock(return_value=(b"Unit not found.", b""))

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            with pytest.raises(ServiceControlFailed) as excinfo:
                await SystemctlManager().control("status", "lyra")

        assert excinfo.value.reason == "not_available"

    @pytest.mark.asyncio
    async def test_restart_nonzero_raises_subprocess_error(self):
        proc = MagicMock()
        proc.returncode = 1
        proc.communicate = AsyncMock(return_value=(b"failed", b""))

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            with pytest.raises(ServiceControlFailed) as excinfo:
                await SystemctlManager().control("restart", "lyra")

        assert excinfo.value.reason == "subprocess_error"

    @pytest.mark.asyncio
    async def test_timeout_raises(self):
        """asyncio.TimeoutError from wait_for propagates as ServiceControlFailed."""
        proc = MagicMock()
        proc.kill = MagicMock()
        proc.wait = AsyncMock()

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            with patch(
                "lyra.integrations.systemctl.asyncio.wait_for",
                side_effect=asyncio.TimeoutError,
            ):
                with pytest.raises(ServiceControlFailed) as excinfo:
                    await SystemctlManager().control("status", None)
                assert excinfo.value.reason == "timeout"

    @pytest.mark.asyncio
    async def test_service_none_with_restart_raises(self):
        """service=None is only valid for status; other actions must raise."""
        with pytest.raises(ServiceControlFailed):
            await SystemctlManager().control("restart", None)

    @pytest.mark.asyncio
    async def test_status_handles_non_utf8_output(self):
        """Non-UTF-8 bytes in systemctl output are replaced, not raised."""
        proc = MagicMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(b"\xff\xfe garbage output", b""))

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            result = await SystemctlManager().control("status", "lyra")

        # Result must be a non-empty string (replacement char or decoded content)
        assert isinstance(result, str)
        assert len(result) > 0
