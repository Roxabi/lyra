"""Identity linking commands — /link and /unlink (#472)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from lyra.core.message import InboundMessage, Response
from lyra.core.stores.identity_alias_store import IdentityAliasStore

if TYPE_CHECKING:
    from lyra.core.pool import Pool

log = logging.getLogger(__name__)

_ADMIN_ONLY = "This command requires admin privileges."


def _get_alias_store(pool: Pool) -> IdentityAliasStore | None:
    """Retrieve alias store from the hub context."""
    hub = getattr(pool, "_ctx", None)
    if hub is None:
        return None
    return getattr(hub, "_alias_store", None)


async def cmd_link(msg: InboundMessage, pool: Pool, args: list[str]) -> Response:
    """Link identities across platforms.

    No args: initiate challenge (generate code).
    With args: complete challenge (validate code from another platform).
    """
    alias_store = _get_alias_store(pool)
    if alias_store is None:
        return Response(content="Identity linking is not available.")

    if not msg.is_admin:
        return Response(content=_ADMIN_ONLY)

    if not args:
        # Initiate: generate challenge code
        code = await alias_store.create_challenge(
            initiator_id=msg.user_id,
            platform=msg.platform,
        )
        return Response(
            content=f"Identity link initiated.\n\n"
            f"Send this command from your other platform within 5 minutes:\n"
            f"`/link {code}`"
        )

    # Complete: validate code
    code = args[0]
    valid, initiator_id, initiator_platform = await alias_store.validate_challenge(code)

    if not valid:
        return Response(content="Invalid or expired link code.")

    # Check same platform (linking must be cross-platform)
    if msg.platform == initiator_platform:
        return Response(
            content="You must run `/link <code>` from a different platform"
            " than where you initiated."
        )

    # Check neither is BLOCKED
    # (We rely on the trust middleware — if msg arrived, user isn't blocked.
    # But check the initiator's stored trust via the auth store on hub.)
    hub = getattr(pool, "_ctx", None)
    if hub is not None:
        auth = hub._authenticators.get((msg.platform, msg.bot_id))
        if auth is not None:
            from lyra.core.trust import TrustLevel
            # Check initiator isn't blocked
            if auth._store and auth._store.check(initiator_id) == TrustLevel.BLOCKED:
                return Response(
                    content="Cannot link: the initiating identity is blocked."
                )
            # Check completer isn't blocked
            if auth._store and auth._store.check(msg.user_id) == TrustLevel.BLOCKED:
                return Response(content="Cannot link: your identity is blocked.")

    # Create the alias (initiator is primary)
    await alias_store.link(primary_id=initiator_id, secondary_id=msg.user_id)

    log.info("Identity linked: %s (primary) <- %s", initiator_id, msg.user_id)
    return Response(
        content=f"Identity linked successfully.\n"
        f"Primary: `{initiator_id}`\n"
        f"Linked: `{msg.user_id}`\n\n"
        f"Your trust level, preferences, and memory are now shared across platforms."
    )


async def cmd_unlink(msg: InboundMessage, pool: Pool, args: list[str]) -> Response:
    """Remove the cross-platform identity link for the current user."""
    alias_store = _get_alias_store(pool)
    if alias_store is None:
        return Response(content="Identity linking is not available.")

    if not msg.is_admin:
        return Response(content=_ADMIN_ONLY)

    removed = await alias_store.unlink(msg.user_id)
    if removed:
        log.info("Identity unlinked: %s", msg.user_id)
        return Response(content=f"Identity link removed for `{msg.user_id}`.")
    return Response(content="No identity link found for your account.")
