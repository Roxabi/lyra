"""Tests for EmbeddedNats lifecycle manager (#520)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.bootstrap.infra.embedded_nats import EmbeddedNats


class TestEmbeddedNatsStart:
    """Tests for EmbeddedNats.start()."""

    async def test_binary_not_found_raises(self) -> None:
        """FileNotFoundError when nats-server is not on PATH."""
        en = EmbeddedNats()
        _which = "lyra.bootstrap.infra.embedded_nats.shutil.which"
        with patch(_which, return_value=None):
            with pytest.raises(FileNotFoundError, match="nats-server binary not found"):
                await en.start()

    async def test_start_spawns_subprocess(self) -> None:
        """start() spawns nats-server and registers atexit handler."""
        en = EmbeddedNats(port=14222)
        mock_proc = AsyncMock()
        mock_proc.pid = 99999
        mock_proc.returncode = None

        which_patch = patch(
            "lyra.bootstrap.infra.embedded_nats.shutil.which",
            return_value="/usr/bin/nats-server",
        )
        exec_patch = patch(
            "lyra.bootstrap.infra.embedded_nats.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        )
        atexit_patch = patch("lyra.bootstrap.infra.embedded_nats.atexit.register")
        with which_patch, exec_patch as mock_exec, atexit_patch as mock_atexit:
            await en.start()

        assert en.process is mock_proc
        assert en.url == "nats://127.0.0.1:14222"
        mock_exec.assert_called_once()
        # Verify nats-server args include port
        call_args = mock_exec.call_args
        assert "-p" in call_args[0]
        assert "14222" in call_args[0]
        mock_atexit.assert_called_once_with(en._kill_sync)


class TestEmbeddedNatsWaitReady:
    """Tests for EmbeddedNats.wait_ready()."""

    async def test_ready_on_first_try(self) -> None:
        """Returns immediately when port is already accepting connections."""
        en = EmbeddedNats()
        en.process = MagicMock()
        en.process.pid = 12345
        en.process.returncode = None

        mock_writer = MagicMock()
        mock_writer.close = MagicMock()
        mock_writer.wait_closed = AsyncMock()

        with patch(
            "lyra.bootstrap.infra.embedded_nats.asyncio.open_connection",
            return_value=(MagicMock(), mock_writer),
        ):
            await en.wait_ready(timeout=1.0)

    async def test_timeout_raises_runtime_error(self) -> None:
        """RuntimeError when nats-server doesn't become ready within timeout."""
        en = EmbeddedNats()
        en.process = MagicMock()
        en.process.pid = 12345
        en.process.returncode = None

        with patch(
            "lyra.bootstrap.infra.embedded_nats.asyncio.open_connection",
            side_effect=OSError("refused"),
        ):
            with pytest.raises(RuntimeError, match="not ready within"):
                await en.wait_ready(timeout=0.2)

    async def test_process_died_raises_with_stderr(self) -> None:
        """RuntimeError includes stderr when nats-server exits before ready."""
        en = EmbeddedNats()
        en.process = MagicMock()
        en.process.pid = 12345
        en.process.returncode = 1
        en.process.stderr = AsyncMock()
        en.process.stderr.read = AsyncMock(return_value=b"address already in use")

        with pytest.raises(RuntimeError, match="address already in use"):
            await en.wait_ready(timeout=0.2)


class TestEmbeddedNatsStop:
    """Tests for EmbeddedNats.stop()."""

    async def test_stop_terminates_process(self) -> None:
        """stop() calls terminate() and waits for exit."""
        en = EmbeddedNats()
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.returncode = None
        mock_proc.terminate = MagicMock()

        async def fake_wait():
            mock_proc.returncode = 0

        mock_proc.wait = fake_wait
        en.process = mock_proc
        en._atexit_registered = True

        with patch("lyra.bootstrap.infra.embedded_nats.atexit.unregister"):
            await en.stop()

        mock_proc.terminate.assert_called_once()

    async def test_stop_kills_on_timeout(self) -> None:
        """stop() force-kills if terminate doesn't exit within 3s."""
        en = EmbeddedNats()
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.returncode = None
        mock_proc.terminate = MagicMock()
        mock_proc.kill = MagicMock()

        slow_wait_done = asyncio.Event()

        async def slow_wait():
            await slow_wait_done.wait()  # explicit: blocks until killed

        async def fast_wait():
            pass

        mock_proc.wait = slow_wait
        en.process = mock_proc
        en._atexit_registered = True

        with patch("lyra.bootstrap.infra.embedded_nats.atexit.unregister"):
            # After kill, switch to fast wait
            original_kill = mock_proc.kill

            def kill_and_switch():
                original_kill()
                mock_proc.wait = fast_wait
                mock_proc.returncode = -9

            mock_proc.kill = kill_and_switch
            await en.stop()

    async def test_stop_noop_when_no_process(self) -> None:
        """stop() is a no-op when process was never started."""
        en = EmbeddedNats()
        await en.stop()  # should not raise

    async def test_stop_noop_when_already_exited(self) -> None:
        """stop() is a no-op when process already exited."""
        en = EmbeddedNats()
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_proc.returncode = 0
        en.process = mock_proc
        en._atexit_registered = True

        with patch("lyra.bootstrap.infra.embedded_nats.atexit.unregister"):
            await en.stop()


class TestAtexit:
    """Tests for atexit orphan protection."""

    def test_kill_sync_kills_running_process(self) -> None:
        """_kill_sync kills a running process."""
        en = EmbeddedNats()
        mock_proc = MagicMock()
        mock_proc.returncode = None
        en.process = mock_proc
        en._kill_sync()
        mock_proc.kill.assert_called_once()

    def test_kill_sync_noop_when_already_exited(self) -> None:
        """_kill_sync is a no-op when process already exited."""
        en = EmbeddedNats()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        en.process = mock_proc
        en._kill_sync()
        mock_proc.kill.assert_not_called()
