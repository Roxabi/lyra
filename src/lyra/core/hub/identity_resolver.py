"""Identity and binding resolution logic.

Extracted from Hub to enforce Clean Architecture:
- Pure functions (no I/O)
- No infrastructure dependencies
- Single responsibility: resolve identity/binding from inbound message
"""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING

from ..auth.identity import Identity
from ..auth.trust import TrustLevel
from ..messaging.message import InboundMessage, Platform
from .hub_protocol import Binding, RoutingKey

if TYPE_CHECKING:
    from ..auth.authenticator import Authenticator

log = logging.getLogger(__name__)

__all__ = ["IdentityResolver"]


class IdentityResolver:
    """Resolves authenticated identity and binding from inbound messages.

    Pure resolution logic with no I/O dependencies. The resolver is owned by
    Hub and receives the authenticator and binding registries at construction.
    """

    def __init__(
        self,
        authenticators: dict[tuple[Platform, str], Authenticator],
        bindings: dict[RoutingKey, Binding],
    ) -> None:
        self._authenticators = authenticators
        self._bindings = bindings

    def _get_authenticator(
        self, platform: Platform, bot_id: str
    ) -> Authenticator | None:
        """Return the registered Authenticator for (platform, bot_id), or None."""
        return self._authenticators.get((platform, bot_id))

    def resolve_identity(
        self, user_id: str | None, platform: str, bot_id: str
    ) -> Identity:
        """Resolve identity for a user on a given (platform, bot_id).

        Used by out-of-band adapter gates (e.g. Discord slash commands) that
        don't flow through the inbound message bus. Returns PUBLIC identity
        when no authenticator is registered for the pair.
        """
        try:
            key_platform = Platform(platform)
        except ValueError:
            return Identity(
                user_id=user_id or "", trust_level=TrustLevel.PUBLIC, is_admin=False
            )
        auth = self._get_authenticator(key_platform, bot_id)
        if auth is None:
            return Identity(
                user_id=user_id or "", trust_level=TrustLevel.PUBLIC, is_admin=False
            )
        return auth.resolve(user_id)

    def resolve_binding(self, msg: InboundMessage) -> Binding | None:
        """Resolve binding: exact key, then wildcard fallback, else None.

        Returns None for invalid platform strings (consistent with resolve_identity
        and resolve_message_trust graceful degradation).
        """
        try:
            platform = Platform(msg.platform)
        except ValueError:
            return None
        scope = msg.scope_id
        key = RoutingKey(platform, msg.bot_id, scope)
        exact = self._bindings.get(key)
        if exact is not None:
            return exact
        wb = self._bindings.get(RoutingKey(platform, msg.bot_id, "*"))
        if wb is not None:
            pid = RoutingKey(platform, msg.bot_id, scope).to_pool_id()
            return Binding(agent_name=wb.agent_name, pool_id=pid)
        return None

    def resolve_message_trust(self, msg: InboundMessage) -> InboundMessage:
        """Re-resolve trust level on the Hub side (C3 -- trust re-resolution).

        Adapters send messages with trust_level=PUBLIC (raw). This method overwrites
        with the authoritative resolved identity before the pipeline sees the message.
        No-op when no authenticator is registered for the (platform, bot_id) pair.
        """
        try:
            key_platform = Platform(msg.platform)
        except ValueError:
            return msg
        auth = self._get_authenticator(key_platform, msg.bot_id)
        if auth is None:
            log.debug(
                "no authenticator for %s/%s -- trust unchanged",
                key_platform,
                msg.bot_id,
            )
            return msg
        uid = msg.user_id if msg.user_id else None
        roles = list(getattr(msg, "roles", ()))
        identity = auth.resolve(uid, roles=roles)
        trust_unchanged = identity.trust_level == msg.trust_level
        admin_unchanged = identity.is_admin == msg.is_admin
        if trust_unchanged and admin_unchanged:
            return msg
        return dataclasses.replace(
            msg, trust_level=identity.trust_level, is_admin=identity.is_admin
        )
