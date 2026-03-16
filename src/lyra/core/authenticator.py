"""Authenticator: per-adapter identity resolver.

Renamed from AuthMiddleware. Resolves user identity (TrustLevel + admin status)
from store, role map, and config. Returns Identity instead of bare TrustLevel.

Usage:
    auth = Authenticator.from_config(raw_config, "telegram", store=auth_store)
    identity = auth.resolve(user_id, roles=role_names, command=command_name)
    # identity.trust_level, identity.is_admin, identity.user_id
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

from lyra.core.identity import Identity
from lyra.core.trust import TrustLevel

if TYPE_CHECKING:
    from lyra.core.auth_store import AuthStore

log = logging.getLogger(__name__)

__all__ = ["Authenticator", "_ALLOW_ALL", "_DENY_ALL"]


# Ordering used to pick the highest trust level among multiple role matches.
_TRUST_ORDER: dict[TrustLevel, int] = {
    TrustLevel.OWNER: 3,
    TrustLevel.TRUSTED: 2,
    TrustLevel.PUBLIC: 1,
    TrustLevel.BLOCKED: 0,
}


class Authenticator:
    """Identity resolver injected into channel adapters.

    Resolution order for resolve():
      1. user_id is None -> BLOCKED (anonymous/service)
      2. store.check(user_id) == BLOCKED -> BLOCKED
      3. command in public_commands -> PUBLIC
      4. store.check(user_id) -> OWNER/TRUSTED if stored
      5. role_map lookup (highest trust level across matched roles)
      6. self._default (fallback)
    """

    def __init__(
        self,
        store: AuthStore | None,
        role_map: dict[str, TrustLevel],
        default: TrustLevel,
        public_commands: list[str] | None = None,
        admin_user_ids: frozenset[str] = frozenset(),
    ) -> None:
        self._store = store
        self._role_map = role_map
        self._default = default
        self._public_commands: frozenset[str] = frozenset(
            public_commands if public_commands is not None else ["/join"]
        )
        self._admin_user_ids = admin_user_ids

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

    def _resolve_trust(
        self,
        user_id: str | None,
        roles: Sequence[str] = (),
        command: str | None = None,
    ) -> TrustLevel:
        """Resolve TrustLevel (same logic as former AuthMiddleware.check)."""
        if user_id is None:
            return TrustLevel.BLOCKED

        stored = self._store_level(user_id)
        if stored == TrustLevel.BLOCKED:
            return TrustLevel.BLOCKED

        if command is not None and command in self._public_commands:
            return TrustLevel.PUBLIC

        if stored is not None:
            return stored

        best = self._best_role_level(roles)
        if best is not None:
            return best

        return self._default

    def check(
        self,
        user_id: str | None,
        roles: Sequence[str] = (),
        command: str | None = None,
    ) -> TrustLevel:
        """Backward-compat alias for resolve(). Returns TrustLevel only."""
        return self._resolve_trust(user_id, roles, command)

    def resolve(
        self,
        user_id: str | None,
        roles: Sequence[str] = (),
        command: str | None = None,
    ) -> Identity:
        """Return the Identity for the given user_id and optional roles.

        Args:
            user_id: Platform user identifier, or None for anonymous/service.
            roles: Role names (e.g. Discord guild roles) for role-based lookup.
            command: Command name (e.g. "/join") -- public_commands bypass.

        Returns:
            Identity with resolved trust_level and is_admin.
        """
        trust = self._resolve_trust(user_id, roles, command)
        resolved_uid = user_id or ""
        is_admin = bool(
            user_id
            and (user_id in self._admin_user_ids or trust == TrustLevel.OWNER)
        )
        return Identity(user_id=resolved_uid, trust_level=trust, is_admin=is_admin)

    @classmethod
    def _cli_sentinel(
        cls,
        store: AuthStore | None,
        admin_user_ids: frozenset[str] = frozenset(),
    ) -> Authenticator:
        """Return a fixed OWNER authenticator for the CLI section."""
        return cls(
            store=store,
            role_map={},
            default=TrustLevel.OWNER,
            admin_user_ids=admin_user_ids,
        )

    @classmethod
    def _build_from_section_cfg(
        cls,
        section_cfg: dict,
        context_label: str,
        store: AuthStore | None,
        admin_user_ids: frozenset[str] = frozenset(),
    ) -> Authenticator:
        """Validate *section_cfg* and build an Authenticator instance."""
        raw_default: str = section_cfg.get("default", "")
        try:
            default = TrustLevel(raw_default)
        except ValueError:
            valid = ", ".join(t.value for t in TrustLevel)
            raise ValueError(
                f"Invalid default '{raw_default}' in {context_label}"
                f" -- must be one of: {valid}"
            )

        role_map: dict[str, TrustLevel] = {}
        for role in section_cfg.get("trusted_roles", []):
            role_map[str(role)] = TrustLevel.TRUSTED

        return cls(
            store=store,
            role_map=role_map,
            default=default,
            admin_user_ids=admin_user_ids,
        )

    @classmethod
    def from_config(
        cls,
        raw: dict,
        section: str,
        store: AuthStore | None = None,
        admin_user_ids: frozenset[str] = frozenset(),
    ) -> Authenticator | None:
        """Parse raw TOML config dict and build an Authenticator instance.

        Returns:
            Authenticator instance, or None if the section is missing (non-CLI).
        """
        auth_block: dict = raw.get("auth", {})
        section_cfg: dict | None = auth_block.get(section)

        if section_cfg is None:
            if section == "cli":
                return cls._cli_sentinel(store=None, admin_user_ids=admin_user_ids)
            log.warning(
                "Missing [auth.%s] in lyra.toml -- %s adapter will be disabled",
                section,
                section,
            )
            return None

        return cls._build_from_section_cfg(
            section_cfg,
            context_label=f"[auth.{section}]",
            store=store,
            admin_user_ids=admin_user_ids,
        )

    @classmethod
    def from_bot_config(
        cls,
        raw: dict,
        section: str,
        bot_id: str,
        store: AuthStore | None = None,
        admin_user_ids: frozenset[str] = frozenset(),
    ) -> Authenticator | None:
        """Parse per-bot auth config from [[auth.<section>_bots]] array.

        Returns:
            Authenticator instance, or None if no matching entry found.
        """
        auth_block: dict = raw.get("auth", {})
        bots_key = f"{section}_bots"
        bots_list: list[dict] = auth_block.get(bots_key, [])

        section_cfg: dict | None = None
        for entry in bots_list:
            if entry.get("bot_id") == bot_id:
                section_cfg = entry
                break

        if section_cfg is None:
            if section == "cli":
                return cls._cli_sentinel(store=None, admin_user_ids=admin_user_ids)
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

        return cls._build_from_section_cfg(
            section_cfg,
            context_label=f"auth config for {section} bot_id={bot_id!r}",
            store=store,
            admin_user_ids=admin_user_ids,
        )


# Sentinel: denies all traffic by default (safe default when no auth is configured).
_DENY_ALL = Authenticator(store=None, role_map={}, default=TrustLevel.BLOCKED)

# Sentinel: allows all traffic as PUBLIC (for tests and permissive contexts).
_ALLOW_ALL = Authenticator(store=None, role_map={}, default=TrustLevel.PUBLIC)
