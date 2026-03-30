"""SupervisorctlManager — ServiceManager backed by supervisorctl.sh (issue #362).

Wraps the supervisorctl.sh script behind the ServiceManager protocol.
Path resolution and timeout are encapsulated here.
"""

from __future__ import annotations

import asyncio
import logging
from asyncio.subprocess import PIPE, STDOUT
from pathlib import Path

from lyra.integrations.base import ServiceControlFailed

log = logging.getLogger(__name__)

_SUPERVISORCTL = str(
    Path.home() / "projects" / "lyra-stack" / "scripts" / "supervisorctl.sh"
)


class SupervisorctlManager:
    """ServiceManager backed by supervisorctl.sh."""

    async def control(self, action: str, service: str | None) -> str:
        """Execute a supervisorctl action.

        Args:
            action: One of restart, start, stop, status.
            service: Service name, or None for all (status only).

        Returns:
            Stdout from the supervisorctl command.

        Raises:
            ServiceControlFailed("not_available")    — script not found.
            ServiceControlFailed("subprocess_error") — non-zero exit.
            ServiceControlFailed("timeout")          — exceeded 10s timeout.
        """
        cmd = [_SUPERVISORCTL, action]
        if service is not None:
            cmd.append(service)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=PIPE,
                stderr=STDOUT,
            )
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise ServiceControlFailed("timeout")
            if proc.returncode != 0:
                output = stdout.decode().strip() if stdout else ""
                log.warning(
                    "SupervisorctlManager: exited %d: %s",
                    proc.returncode,
                    output[:200],
                )
                raise ServiceControlFailed("subprocess_error")
        except FileNotFoundError:
            raise ServiceControlFailed("not_available")

        return stdout.decode().strip() if stdout else ""
