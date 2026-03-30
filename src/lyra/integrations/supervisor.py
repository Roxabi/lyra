"""SupervisorctlManager — ServiceManager backed by supervisorctl.sh (issue #362).

Wraps the supervisorctl.sh script behind the ServiceManager protocol.
Path resolution and timeout are encapsulated here.

Override script path with LYRA_SUPERVISORCTL_PATH env var.
Default: ~/projects/lyra-stack/scripts/supervisorctl.sh
"""

from __future__ import annotations

import asyncio
import logging
import os
from asyncio.subprocess import PIPE, STDOUT
from pathlib import Path

from lyra.integrations.base import ServiceControlFailed

log = logging.getLogger(__name__)

_DEFAULT_SUPERVISORCTL = (
    Path.home() / "projects" / "lyra-stack" / "scripts" / "supervisorctl.sh"
)

_TRUSTED_BASE = Path.home() / "projects"


class SupervisorctlManager:
    """ServiceManager backed by supervisorctl.sh."""

    def __init__(self, script_path: Path | None = None) -> None:
        raw = os.environ.get("LYRA_SUPERVISORCTL_PATH")
        if raw:
            resolved = Path(raw).expanduser().resolve()
            if not resolved.is_relative_to(_TRUSTED_BASE):
                raise ValueError(
                    f"LYRA_SUPERVISORCTL_PATH resolves to {resolved!r}, "
                    f"which is outside the trusted base {_TRUSTED_BASE!r}."
                )
            self._script = str(resolved)
        else:
            self._script = str(script_path or _DEFAULT_SUPERVISORCTL)

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
        cmd = [self._script, action]
        if service is not None:
            cmd.append(service)

        stdout: bytes = b""
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
