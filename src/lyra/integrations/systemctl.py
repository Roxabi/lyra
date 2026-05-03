"""SystemctlManager — ServiceManager backed by `systemctl --user` (Quadlet model).

Replaces the supervisord-era `SupervisorctlManager` for the `/svc` plugin. Maps
the user-facing service name to one or more rootless `systemd --user` units:

    lyra         → lyra-{nats,hub,telegram,discord,clipool}.service
    voicecli_stt → voicecli-stt.service
    voicecli_tts → voicecli-tts.service

`status` with no service returns the combined status of every known unit.
"""

from __future__ import annotations

import asyncio
import logging
from asyncio.subprocess import PIPE, STDOUT

from lyra.integrations.base import ServiceControlFailed

log = logging.getLogger(__name__)

_TIMEOUT_S = 10.0

_LYRA_UNITS = (
    "lyra-nats.service",
    "lyra-hub.service",
    "lyra-telegram.service",
    "lyra-discord.service",
    "lyra-clipool.service",
)

_SERVICE_UNITS: dict[str, tuple[str, ...]] = {
    "lyra": _LYRA_UNITS,
    "voicecli_stt": ("voicecli-stt.service",),
    "voicecli_tts": ("voicecli-tts.service",),
}

_ACTIONS: dict[str, str] = {
    "restart": "restart",
    "start": "start",
    "stop": "stop",
    "status": "status",
}


class SystemctlManager:
    """ServiceManager backed by `systemctl --user`."""

    async def control(self, action: str, service: str | None) -> str:
        sysd_action = _ACTIONS.get(action)
        if sysd_action is None:
            raise ServiceControlFailed("subprocess_error")

        if service is None:
            if action != "status":
                raise ServiceControlFailed("subprocess_error")
            units = tuple(u for group in _SERVICE_UNITS.values() for u in group)
        else:
            units = _SERVICE_UNITS.get(service, ())
            if not units:
                raise ServiceControlFailed("subprocess_error")

        cmd = ["systemctl", "--user", sysd_action, *units]

        stdout: bytes = b""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=PIPE,
                stderr=STDOUT,
            )
            try:
                stdout, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=_TIMEOUT_S
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise ServiceControlFailed("timeout")
            # systemctl status exits non-zero when units are inactive — that's
            # still useful output, not an error. For mutating actions, non-zero
            # is a real failure.
            if proc.returncode != 0 and action != "status":
                output = stdout.decode().strip() if stdout else ""
                log.warning(
                    "SystemctlManager: %s exited %d: %s",
                    action,
                    proc.returncode,
                    output[:200],
                )
                raise ServiceControlFailed("subprocess_error")
        except FileNotFoundError:
            raise ServiceControlFailed("not_available")

        return stdout.decode().strip() if stdout else ""
