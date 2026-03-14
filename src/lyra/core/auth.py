"""AuthMiddleware + TrustLevel: per-adapter authorization gate.

Usage:
    auth = AuthMiddleware.from_config(raw_config, "telegram", store=auth_store)
    trust = auth.check(user_id, roles=role_names, command=command_name)
    if trust == TrustLevel.BLOCKED:
        return  # reject before normalize()
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

from lyra.core.trust import TrustLevel

if TYPE_CHECKING:
    from lyra.core.auth_store import AuthStore

log = logging.getLogger(__name__)

__all__ = ["AuthMiddleware", "TrustLevel"]


# Ordering used to pick the highest trust level among multiple role matches.
_TRUST_ORDER: dict[TrustLevel, int] = {
    TrustLevel.OWNER: 3,
    TrustLevel.TRUSTED: 2,
    TrustLevel.PUBLIC: 1,
    TrustLevel.BLOCKED: 0,
}


class AuthMiddleware:
    """Authorization gate injected into channel adapters.

    Resolution order for check():
      1. command in public_commands -> TrustLevel.PUBLIC (bypasses all other checks)
      2. store.check(user_id) -- if store returns non-default level, use it
      3. role_map lookup (highest trust level across all matched roles)
      4. self._default (fallback)
    """

    def __init__(
        self,
        store: AuthStore | None,
        role_map: dict[str, TrustLevel],
        default: TrustLevel,
        public_commands: list[str] | None = None,
    ) -> None:
        self._store = store
        self._role_map = role_map
        self._default = default
        self._public_commands: frozenset[str] = frozenset(
            public_commands if public_commands is not None else ["/join"]
        )

    def _store_level(self, user_id: str | None) -> TrustLevel | None:
        """Return the stored TrustLevel for user_id, or None if not present."""
        if self._store is None or user_id is None:
            return None
        level = self._store.check(user_id)
        if level in {TrustLevel.OWNER, TrustLevel.TRUSTED, TrustLevel.BLOCKED}:
            return level
        return None

    def _best_role_level(self, roles: Sequence[str]) -> TrustLevel | None:
        """Return the highest TrustLevel from role_map for the given roles."""
        best: TrustLevel | None = None
        for role in roles:
            candidate = self._role_map.get(role)
            if candidate is not None:
                if best is None or _TRUST_ORDER[candidate] > _TRUST_ORDER[best]:
                    best = candidate
        return best

    def check(
        self,
        user_id: str | None,
        roles: Sequence[str] = (),
        command: str | None = None,
    ) -> TrustLevel:
        """Return the TrustLevel for the given user_id and optional roles.

        Args:
            user_id: Platform user identifier, or None for anonymous/service messages.
            roles: Role names (e.g. Discord guild roles) for role-based lookup.
            command: Command name (e.g. "/join") -- public_commands bypass all checks.

        Returns:
            The resolved TrustLevel.
        """
        # BLOCKED check first — even public commands are denied to blocked users
        stored = self._store_level(user_id)
        if stored == TrustLevel.BLOCKED:
            return TrustLevel.BLOCKED

        # (a) Public command bypass -- always allow regardless of trust level
        if command is not None and command in self._public_commands:
            return TrustLevel.PUBLIC

        # (b) AuthStore lookup result (OWNER / TRUSTED already resolved above)
        if stored is not None:
            return stored

        # (c) Role map lookup
        best = self._best_role_level(roles)
        if best is not None:
            return best

        return self._default

    @classmethod
    def from_config(
        cls,
        raw: dict,
        section: str,
        store: AuthStore | None = None,
    ) -> "AuthMiddleware | None":
        """Parse raw TOML config dict and build an AuthMiddleware instance.

        Args:
            raw: Top-level parsed TOML dict (may contain an "auth" key).
            section: Adapter section name, e.g. "telegram", "discord", "cli".
            store: Optional pre-connected AuthStore (seeds applied by caller).

        Returns:
            AuthMiddleware instance, or None if the section is missing (non-CLI).
            Missing section for "cli" never returns None -- returns OWNER middleware.

        Raises:
            ValueError: If the section exists but contains an invalid default value.
        """
        auth_block: dict = raw.get("auth", {})
        section_cfg: dict | None = auth_block.get(section)

        if section_cfg is None:
            if section == "cli":
                # CLI is always local/trusted -- fixed OWNER, no config required.
                return cls(store=None, role_map={}, default=TrustLevel.OWNER)
            log.warning(
                "Missing [auth.%s] in lyra.toml -- %s adapter will be disabled",
                section,
                section,
            )
            return None

        # Validate default
        raw_default: str = section_cfg.get("default", "")
        try:
            default = TrustLevel(raw_default)
        except ValueError:
            valid = ", ".join(t.value for t in TrustLevel)
            raise ValueError(
                f"Invalid default '{raw_default}' in [auth.{section}]"
                f" -- must be one of: {valid}"
            )

        # trusted_roles must contain Discord role snowflake IDs (numeric strings),
        # not display names. Example: trusted_roles = ["123456789012345678"]
        role_map: dict[str, TrustLevel] = {}
        for role in section_cfg.get("trusted_roles", []):
            role_map[str(role)] = TrustLevel.TRUSTED

        return cls(store=store, role_map=role_map, default=default)

    @classmethod
    def from_bot_config(
        cls,
        raw: dict,
        section: str,
        bot_id: str,
        store: AuthStore | None = None,
    ) -> "AuthMiddleware | None":
        """Parse per-bot auth config from [[auth.<section>_bots]] array.

        Looks up the entry with matching bot_id from the array
        ``auth.<section>_bots`` (e.g. ``auth.telegram_bots`` for section="telegram").
        Does NOT fall back to the flat ``auth.<section>`` section -- per-bot entries
        must be explicit. Use ``from_config()`` for the legacy single-bot path.

        Args:
            raw: Top-level parsed TOML dict.
            section: Platform section name, e.g. "telegram", "discord".
            bot_id: The bot_id to look up in the per-bot array.
            store: Optional pre-connected AuthStore (seeds applied by caller).

        Returns:
            AuthMiddleware instance, or None if no matching entry found (bot disabled).

        Raises:
            ValueError: If the entry exists but contains an invalid default value.
        """
        auth_block: dict = raw.get("auth", {})
        bots_key = f"{section}_bots"
        bots_list: list[dict] = auth_block.get(bots_key, [])

        # Find matching entry by bot_id -- no flat-section fallback to prevent
        # cross-bot trust bleed (a bot without an explicit entry is disabled).
        section_cfg: dict | None = None
        for entry in bots_list:
            if entry.get("bot_id") == bot_id:
                section_cfg = entry
                break

        if section_cfg is None:
            if section == "cli":
                return cls(store=None, role_map={}, default=TrustLevel.OWNER)
            log.warning(
                "Missing [auth.%s] or [[auth.%s]] entry for bot_id=%r"
                " -- %s adapter bot_id=%r will be disabled",
                section,
                bots_key,
                bot_id,
                section,
                bot_id,
            )
            return None

        raw_default: str = section_cfg.get("default", "")
        try:
            default = TrustLevel(raw_default)
        except ValueError:
            valid = ", ".join(t.value for t in TrustLevel)
            raise ValueError(
                f"Invalid default '{raw_default}' in auth config"
                f" for {section} bot_id={bot_id!r}"
                f" -- must be one of: {valid}"
            )

        role_map: dict[str, TrustLevel] = {}
        for role in section_cfg.get("trusted_roles", []):
            role_map[str(role)] = TrustLevel.TRUSTED

        return cls(store=store, role_map=role_map, default=default)
