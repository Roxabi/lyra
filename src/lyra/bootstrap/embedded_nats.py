"""Embedded nats-server lifecycle manager.

Auto-starts a local nats-server subprocess when NATS_URL is not set,
providing zero-config NATS for development and single-machine deployments.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import shutil
import socket

log = logging.getLogger(__name__)

_DEFAULT_PORT = 4222


class EmbeddedNats:
    """Manage an embedded nats-server subprocess lifecycle."""

    def __init__(self, port: int = _DEFAULT_PORT) -> None:
        self.port = port
        self.url = f"nats://127.0.0.1:{port}"
        self.process: asyncio.subprocess.Process | None = None
        self._atexit_registered = False

    async def start(self) -> None:
        """Start the embedded nats-server.

        Raises:
            FileNotFoundError: if nats-server binary is not on PATH.
            RuntimeError: if port is already in use or server fails to start.
        """
        binary = shutil.which("nats-server")
        if binary is None:
            raise FileNotFoundError(
                "nats-server binary not found. Install it:\n"
                "        cd ~/projects/lyra && make nats-install\n"
                "        Or set NATS_URL to use an external NATS server."
            )

        self.process = await asyncio.create_subprocess_exec(
            binary,
            "-a", "127.0.0.1",
            "-p", str(self.port),
            "--no_auth",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        # Register atexit to kill orphans if parent crashes
        atexit.register(self._kill_sync)
        self._atexit_registered = True

        log.info(
            "Embedded nats-server starting on port %d (PID %d)",
            self.port,
            self.process.pid,
        )

    async def wait_ready(self, timeout: float = 5.0) -> None:
        """Poll TCP until nats-server accepts connections.

        Raises:
            RuntimeError: if server doesn't become ready within timeout.
        """
        interval = 0.1
        elapsed = 0.0
        while elapsed < timeout:
            try:
                # Quick TCP connect check
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.5):
                    pid = self.process.pid if self.process else 0
                    log.info("Embedded nats-server ready (PID %d)", pid)
                    return
            except OSError:
                await asyncio.sleep(interval)
                elapsed += interval

        # Check if process died
        if self.process and self.process.returncode is not None:
            if self.process.stderr:
                stderr_bytes = await self.process.stderr.read()
            else:
                stderr_bytes = b""
            stderr_text = stderr_bytes.decode(errors="replace").strip()
            raise RuntimeError(
                f"nats-server exited with code {self.process.returncode}: {stderr_text}"
            )

        raise RuntimeError(
            f"nats-server not ready within {timeout}s — port {self.port} busy.\n"
            "        Set NATS_URL to connect to an existing NATS server instead."
        )

    async def stop(self) -> None:
        """Gracefully stop the embedded nats-server."""
        if self.process is None:
            return

        if self.process.returncode is not None:
            log.debug(
                "Embedded nats-server already exited (code %d)",
                self.process.returncode,
            )
            self._deregister_atexit()
            return

        self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            log.warning("nats-server did not stop within 3s — killing")
            self.process.kill()
            await self.process.wait()

        log.info("Embedded nats-server stopped (PID %d)", self.process.pid)
        self._deregister_atexit()

    def _kill_sync(self) -> None:
        """Synchronous kill for atexit — last resort orphan protection."""
        if self.process and self.process.returncode is None:
            self.process.kill()

    def _deregister_atexit(self) -> None:
        """Remove the atexit handler after clean shutdown."""
        if self._atexit_registered:
            atexit.unregister(self._kill_sync)
            self._atexit_registered = False
