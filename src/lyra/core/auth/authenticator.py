"""Authenticator: per-adapter identity resolver.

Resolves user identity (TrustLevel + admin status) from store, role map, and config.
Returns Identity instead of bare TrustLevel.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING

from lyra.core.auth.identity import Identity
from lyra.core.auth.trust import TrustLevel

if TYPE_CHECKING:
    from lyra.infrastructure.stores.auth_store import AuthStore
    from lyra.infrastructure.stores.identity_alias_store import IdentityAliasStore

log = logging.getLogger(__name__)

__all__ = ["Authenticator", "_ALLOW_ALL", "_DENY_ALL"]


_TRUST_ORDER: dict[TrustLevel, int] = {
    TrustLevel.OWNER: 3,
    TrustLevel.TRUSTED: 2,
    TrustLevel.PUBLIC: 1,
    TrustLevel.BLOCKED: 0,
}


class Authenticator:
    """Identity resolver: trust resolution considers all linked aliases.

    Order: blocked (any alias) → public_commands bypass → max stored trust
    → role_map → default.
    """

    def __init__(  # noqa: PLR0913
        self,
        store: AuthStore | None,
        role_map: dict[str, TrustLevel],
        default: TrustLevel,
        public_commands: list[str] | None = None,
        admin_user_ids: frozenset[str] = frozenset(),
        alias_store: IdentityAliasStore | None = None,
    ) -> None:
        self._store = store
        self._role_map = role_map
        self._default = default
        self._public_commands: frozenset[str] = frozenset(
            public_commands if public_commands is not None else ["/join"]
        )
        self._admin_user_ids = admin_user_ids
        self._alias_store = alias_store

    def _store_level(self, user_id: str | None) -> TrustLevel | None:
        if self._store is None or user_id is None:
            return None
        level = self._store.check(user_id)
        if level in {TrustLevel.OWNER, TrustLevel.TRUSTED, TrustLevel.BLOCKED}:
            return level
        return None

    def _best_role_level(self, roles: Sequence[str]) -> TrustLevel | None:
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
        if user_id is None:
            return TrustLevel.BLOCKED

        # Resolve all linked identities (alias-aware)
        aliases = (
            self._alias_store.resolve_aliases(user_id)
            if self._alias_store
            else frozenset({user_id})
        )

        # Any linked ID BLOCKED → entire group is BLOCKED
        for a in aliases:
            stored = self._store_level(a)
            if stored == TrustLevel.BLOCKED:
                return TrustLevel.BLOCKED

        # Command bypass check
        if command is not None and command in self._public_commands:
            return TrustLevel.PUBLIC

        # Max trust across all linked identities
        best_stored: TrustLevel | None = None
        for a in aliases:
            stored = self._store_level(a)
            if stored is not None and (
                best_stored is None
                or _TRUST_ORDER[stored] > _TRUST_ORDER.get(best_stored, 0)
            ):
                best_stored = stored

        if best_stored is not None:
            return best_stored

        # admin_user_ids (from [admin].user_ids) use platform-prefixed keys
        # (e.g. "tg:user:123") while seed_from_config stores bare IDs — grant
        # OWNER directly so admins are never blocked by a cache-key mismatch.
        if user_id in self._admin_user_ids:
            return TrustLevel.OWNER

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
        return self._resolve_trust(user_id, roles, command)

    def resolve(
        self,
        user_id: str | None,
        roles: Sequence[str] = (),
        command: str | None = None,
    ) -> Identity:
        """Resolve trust and admin for user_id; return Identity.

        user_id=None → BLOCKED. Considers all aliases via alias_store.
        """
        trust = self._resolve_trust(user_id, roles, command)
        resolved_uid = user_id or ""
        # Resolve aliases once and reuse for admin check.
        # is_admin uses stored trust (before command bypass), not resolved trust.
        # An OWNER user issuing a public command retains is_admin=True.
        aliases = (
            self._alias_store.resolve_aliases(user_id)
            if self._alias_store and user_id
            else frozenset({resolved_uid})
        )
        is_admin = bool(
            user_id
            and any(
                a in self._admin_user_ids or self._store_level(a) == TrustLevel.OWNER
                for a in aliases
            )
        )
        return Identity(user_id=resolved_uid, trust_level=trust, is_admin=is_admin)

    @classmethod
    def _cli_sentinel(
        cls,
        store: AuthStore | None,
        admin_user_ids: frozenset[str] = frozenset(),
        alias_store: IdentityAliasStore | None = None,
    ) -> Authenticator:
        return cls(
            store=store,
            role_map={},
            default=TrustLevel.OWNER,
            admin_user_ids=admin_user_ids,
            alias_store=alias_store,
        )

    @classmethod
    def _build_from_section_cfg(
        cls,
        section_cfg: dict,
        context_label: str,
        store: AuthStore | None,
        admin_user_ids: frozenset[str] = frozenset(),
        alias_store: IdentityAliasStore | None = None,
    ) -> Authenticator:
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
            alias_store=alias_store,
        )

    @classmethod
    def from_config(
        cls,
        raw: dict,
        section: str,
        store: AuthStore | None = None,
        admin_user_ids: frozenset[str] = frozenset(),
        alias_store: IdentityAliasStore | None = None,
    ) -> Authenticator | None:
        auth_block: dict = raw.get("auth", {})
        section_cfg: dict | None = auth_block.get(section)

        if section_cfg is None:
            if section == "cli":
                return cls._cli_sentinel(
                    store=None,
                    admin_user_ids=admin_user_ids,
                    alias_store=alias_store,
                )
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
            alias_store=alias_store,
        )

    @classmethod
    def from_bot_config(  # noqa: PLR0913
        cls,
        raw: dict,
        section: str,
        bot_id: str,
        store: AuthStore | None = None,
        admin_user_ids: frozenset[str] = frozenset(),
        alias_store: IdentityAliasStore | None = None,
    ) -> Authenticator | None:
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
                return cls._cli_sentinel(
                    store=None,
                    admin_user_ids=admin_user_ids,
                    alias_store=alias_store,
                )
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
            alias_store=alias_store,
        )


# Sentinel: denies all traffic by default (safe default when no auth is configured).
_DENY_ALL = Authenticator(store=None, role_map={}, default=TrustLevel.BLOCKED)

# Sentinel: allows all traffic as PUBLIC (for tests and permissive contexts).
# Note: resolve() always returns is_admin=False. For admin identity in tests,
# construct Authenticator directly with admin_user_ids or use trust=OWNER.
_ALLOW_ALL = Authenticator(store=None, role_map={}, default=TrustLevel.PUBLIC)
