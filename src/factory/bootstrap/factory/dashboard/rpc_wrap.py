"""Dashboard RPC wrap — principal gate (ADR-103 Block 5)."""

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


def wrap_dashboard_handler(hub: "Hub", nc: "NATS", handler: Any):
    """Fail-closed principal check, then run business handler."""

    async def _cb(msg: Msg) -> None:
        from factory.core.auth.control_plane_wire import (
            clear_request_principal,
            parse_principal_from_payload,
            set_request_principal,
            strip_principal_payload,
        )

        try:
            payload = json.loads(msg.data.decode()) if msg.data else {}
            principal = parse_principal_from_payload(payload)
            if principal is None:
                err = {
                    "error": "unauthorized",
                    "message": "principal required",
                }
                await msg.respond(json.dumps(err).encode())
                return
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
