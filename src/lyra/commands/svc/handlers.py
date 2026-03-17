"""Service management plugin — wraps supervisorctl (admin-only)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from lyra.core.message import InboundMessage, Response
from lyra.core.pool import Pool

log = logging.getLogger(__name__)

_SUPERVISORCTL = str(
    Path.home() / "projects" / "lyra-stack" / "scripts" / "supervisorctl.sh"
)

_ALLOWED_SERVICES = frozenset({"lyra", "voicecli_stt", "voicecli_tts"})
_ALLOWED_ACTIONS = frozenset({"restart", "start", "stop", "status"})
_ALIASES: dict[str, str] = {"reload": "restart"}

_USAGE = (
    "Usage: /svc <action> [service]\n"
    "Actions: status, restart, start, stop\n"
    f"Services: {', '.join(sorted(_ALLOWED_SERVICES))}\n"
    "Example: /svc restart lyra"
)

_SERVICES_LIST = f"Services: {', '.join(sorted(_ALLOWED_SERVICES))}"


def _build_cmd(action: str, args: list[str]) -> list[str] | Response:
    """Return supervisorctl command list, or a Response on validation error."""
    if action == "status":
        if len(args) >= 2:
            service = args[1].lower()
            if service not in _ALLOWED_SERVICES:
                return Response(
                    content=f"Unknown service '{args[1]}'.\n{_SERVICES_LIST}"
                )
            return [_SUPERVISORCTL, "status", service]
        return [_SUPERVISORCTL, "status"]

    if len(args) < 2:
        return Response(content=f"Usage: /svc {action} <service>\n{_SERVICES_LIST}")
    service = args[1].lower()
    if service not in _ALLOWED_SERVICES:
        return Response(content=f"Unknown service '{args[1]}'.\n{_SERVICES_LIST}")
    return [_SUPERVISORCTL, action, service]


async def cmd_svc(msg: InboundMessage, pool: Pool, args: list[str]) -> Response:
    """Manage supervisor services. Admin-only."""
    if not msg.is_admin:
        return Response(content="This command is admin-only.")

    if not args:
        return Response(content=_USAGE)

    action = _ALIASES.get(args[0].lower(), args[0].lower())
    if action not in _ALLOWED_ACTIONS:
        return Response(content=f"Unknown action '{args[0]}'.\n{_USAGE}")

    cmd = _build_cmd(action, args)
    if isinstance(cmd, Response):
        return cmd

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        output = stdout.decode().strip() if stdout else ""
        return Response(content=output or "(no output)")
    except asyncio.TimeoutError:
        return Response(content="Command timed out.")
    except Exception as exc:
        log.exception("svc plugin error: %s", exc)
        return Response(content=f"Error: {exc}")
