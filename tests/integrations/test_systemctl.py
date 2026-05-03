"""Tests for SystemctlManager (lyra.integrations.systemctl)."""

from __future__ import annotations

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
        with pytest.raises(ServiceControlFailed):
            await SystemctlManager().control("restart", "bogus")

    @pytest.mark.asyncio
    async def test_unknown_action_raises(self):
        with pytest.raises(ServiceControlFailed):
            await SystemctlManager().control("nuke", "lyra")

    @pytest.mark.asyncio
    async def test_systemctl_missing_raises_not_available(self):
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError()):
            with pytest.raises(ServiceControlFailed) as excinfo:
                await SystemctlManager().control("status", "lyra")

        assert excinfo.value.reason == "not_available"

    @pytest.mark.asyncio
    async def test_status_tolerates_nonzero_exit(self):
        proc = MagicMock()
        proc.returncode = 3  # systemctl status exits 3 when units inactive
        proc.communicate = AsyncMock(return_value=(b"some inactive", b""))

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            result = await SystemctlManager().control("status", "lyra")

        assert "inactive" in result

    @pytest.mark.asyncio
    async def test_restart_nonzero_raises_subprocess_error(self):
        proc = MagicMock()
        proc.returncode = 1
        proc.communicate = AsyncMock(return_value=(b"failed", b""))

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            with pytest.raises(ServiceControlFailed) as excinfo:
                await SystemctlManager().control("restart", "lyra")

        assert excinfo.value.reason == "subprocess_error"
