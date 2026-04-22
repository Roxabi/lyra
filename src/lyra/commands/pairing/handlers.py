"""Pairing plugin handlers (issue #103).

Provides /invite, /join, /unpair commands for the unified pairing system.
PairingManager is accessed via the module-level getter in lyra.core.pairing.
"""

from __future__ import annotations

import logging

from lyra.core.messaging.message import InboundMessage, Response
from lyra.core.pool import Pool
from lyra.core.stores.pairing import PairingError, get_pairing_manager

log = logging.getLogger(__name__)

_NOT_ENABLED = "Pairing is not enabled."
_ADMIN_ONLY = "This command is admin-only."


async def cmd_invite(msg: InboundMessage, pool: Pool, args: list[str]) -> Response:
    """Generate a pairing code. Admin-only."""
    pm = get_pairing_manager()
    if pm is None or not pm.config.enabled:
        return Response(content=_NOT_ENABLED)

    if not msg.is_admin:
        return Response(content=_ADMIN_ONLY)

    try:
        code = await pm.generate_code(msg.user_id)
    except PairingError as exc:
        return Response(content=str(exc))

    return Response(
        content=f"Pairing code: {code}\n"
        f"Valid for {pm.config.ttl_seconds // 60} minutes. "
        f"Share this with the user who should join."
    )


async def cmd_join(msg: InboundMessage, pool: Pool, args: list[str]) -> Response:
    """Redeem a pairing code. Rate-limited."""
    pm = get_pairing_manager()
    if pm is None or not pm.config.enabled:
        return Response(content=_NOT_ENABLED)

    if not args:
        return Response(content="Usage: /join <CODE>")

    code = args[0].strip().upper()
    identity_key = msg.user_id

    if not pm.check_rate_limit(identity_key):
        return Response(
            content="Too many failed attempts. Please wait before trying again."
        )

    success, message = await pm.validate_code(code, identity_key)
    if not success:
        pm.record_failed_attempt(identity_key)
    return Response(content=message)


async def cmd_unpair(msg: InboundMessage, pool: Pool, args: list[str]) -> Response:
    """Revoke a user's paired session. Admin-only."""
    pm = get_pairing_manager()
    if pm is None or not pm.config.enabled:
        return Response(content=_NOT_ENABLED)

    if not msg.is_admin:
        return Response(content=_ADMIN_ONLY)

    if not args:
        return Response(content="Usage: /unpair <USER_ID>")

    target_identity = args[0].strip()
    found = await pm.revoke_session(target_identity)

    if found:
        return Response(content=f"Session for {target_identity!r} has been revoked.")
    return Response(content=f"No paired session found for {target_identity!r}.")
