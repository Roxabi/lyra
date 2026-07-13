"""Dashboard RPC wrap — principal gate (ADR-103 Block 5 + auth public subjects)."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from nats.aio.msg import Msg
from pydantic import ValidationError

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

    from factory.core.hub import Hub

log = logging.getLogger(__name__)

# Public identity RPCs (login / resolve / accept-invite) — no principal stamp.
# Keep in sync with SUBJECTS.auth_* public set (ADR-103 Slice 1).
_PUBLIC_AUTH_SUBJECTS: frozenset[str] = frozenset(
    {
        "factory.dashboard.auth.login",
        "factory.dashboard.auth.logout",
        "factory.dashboard.auth.session.resolve",
        "factory.dashboard.auth.invite.accept",
        "factory.dashboard.auth.api_key.resolve",
    }
)


def wrap_dashboard_handler(
    hub: "Hub",
    nc: "NATS",
    handler: Any,
    *,
    require_principal: bool | None = None,
):
    """Fail-closed principal check (unless public auth subject), then handler.

    *require_principal*: force True/False; when None, derive from *msg.subject*
    against :data:`_PUBLIC_AUTH_SUBJECTS`.
    """

    async def _cb(msg: Msg) -> None:
        from factory.core.auth.control_plane_wire import (
            clear_request_principal,
            parse_principal_from_payload,
            set_request_principal,
            strip_principal_payload,
        )

        try:
            payload = json.loads(msg.data.decode()) if msg.data else {}
            need_principal = (
                require_principal
                if require_principal is not None
                else msg.subject not in _PUBLIC_AUTH_SUBJECTS
            )
            principal = parse_principal_from_payload(payload)
            if need_principal and principal is None:
                from factory.dashboard.security import audit_security

                audit_security(
                    "rpc_deny",
                    subject=msg.subject,
                    reason="principal_required",
                )
                err = {
                    "error": "unauthorized",
                    "message": "principal required",
                }
                await msg.respond(json.dumps(err).encode())
                return
            if principal is not None:
                set_request_principal(principal)
            try:
                business = strip_principal_payload(payload)
                result = await handler(hub, nc, business)
                await msg.respond(json.dumps(result).encode())
            finally:
                clear_request_principal()
        except (ValidationError, json.JSONDecodeError, UnicodeDecodeError, KeyError):
            log.exception("dashboard_rpc handler failed subject=%s", msg.subject)
            err = {"error": "bad_request"}
            await msg.respond(json.dumps(err).encode())
        except Exception:  # noqa: BLE001 — DEBT:boundary-broad-catch
            log.exception("dashboard_rpc handler failed subject=%s", msg.subject)
            err = {"error": "internal_error"}
            await msg.respond(json.dumps(err).encode())

    return _cb
