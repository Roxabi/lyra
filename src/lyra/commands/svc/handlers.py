"""Service management plugin — wraps `systemctl --user` (admin-only)."""

from __future__ import annotations

import logging
import re

from lyra.core.error_utils import safe_error_response
from lyra.core.messaging.message import InboundMessage, Response
from lyra.core.pool import Pool
from lyra.integrations.base import ServiceControlFailed
from lyra.integrations.systemctl import SystemctlManager

log = logging.getLogger(__name__)

_service_manager = SystemctlManager()

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


def _sanitize_svc_output(output: str) -> str:
    """Strip PID numbers and absolute paths from systemctl output.

    Prevents internal process IDs and filesystem paths from being surfaced to
    users (L2 — information disclosure via /svc output).
    """
    output = re.sub(r"\bpid \d+", "pid ***", output)
    output = re.sub(r"\(pid \d+\)", "(pid ***)", output)
    output = re.sub(r"/(?:home|var|tmp|etc|usr|opt)/\S+", "<path>", output)
    return output


def _validate(action: str, args: list[str]) -> tuple[str | None, Response | None]:
    """Validate action + service. Returns (service, None) or (None, error Response)."""
    if action == "status":
        if len(args) >= 2:
            service = args[1].lower()
            if service not in _ALLOWED_SERVICES:
                return None, Response(
                    content=f"Unknown service '{args[1]}'.\n{_SERVICES_LIST}"
                )
            return service, None
        return None, None  # status all

    if len(args) < 2:
        return None, Response(
            content=f"Usage: /svc {action} <service>\n{_SERVICES_LIST}"
        )
    service = args[1].lower()
    if service not in _ALLOWED_SERVICES:
        return None, Response(content=f"Unknown service '{args[1]}'.\n{_SERVICES_LIST}")
    return service, None


async def cmd_svc(msg: InboundMessage, pool: Pool, args: list[str]) -> Response:
    """Manage system services via systemctl --user. Admin-only."""
    if not msg.is_admin:
        return Response(content="This command is admin-only.")

    if not args:
        return Response(content=_USAGE)

    action = _ALIASES.get(args[0].lower(), args[0].lower())
    if action not in _ALLOWED_ACTIONS:
        return Response(content=f"Unknown action '{args[0]}'.\n{_USAGE}")

    service, error = _validate(action, args)
    if error is not None:
        return error

    try:
        output = await _service_manager.control(action, service)
        return Response(content=_sanitize_svc_output(output) or "(no output)")
    except ServiceControlFailed as exc:
        if exc.reason == "timeout":
            return Response(content="Command timed out.")
        if exc.reason == "not_available":
            return Response(content="systemctl --user not found.")
        return safe_error_response(exc, log, "svc plugin")
    except Exception as exc:  # noqa: BLE001 — DEBT:boundary-broad-catch
        return safe_error_response(exc, log, "svc plugin")
