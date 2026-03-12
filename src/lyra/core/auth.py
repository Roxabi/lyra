"""AuthMiddleware + TrustLevel: per-adapter authorization gate.

Usage:
    auth = AuthMiddleware.from_config(raw_config, "telegram")
    trust = auth.check(user_id, roles=role_names)
    if trust == TrustLevel.BLOCKED:
        return  # reject before normalize()
"""

from __future__ import annotations

from collections.abc import Sequence

from lyra.core.trust import TrustLevel

__all__ = ["AuthMiddleware", "TrustLevel"]


# Ordering used to pick the highest trust level among multiple role matches.
_TRUST_ORDER: dict[TrustLevel, int] = {
    TrustLevel.OWNER: 3,
    TrustLevel.TRUSTED: 2,
    TrustLevel.PUBLIC: 1,
    TrustLevel.BLOCKED: 0,
}


class AuthMiddleware:
    """Stateless authorization gate injected into channel adapters.

    Resolution order for check():
      1. user_map lookup (explicit user assignment wins)
      2. role_map lookup (highest trust level across all matched roles)
      3. self._default (fallback)
    """

    def __init__(
        self,
        user_map: dict[str, TrustLevel],
        role_map: dict[str, TrustLevel],
        default: TrustLevel,
    ) -> None:
        self._user_map = user_map
        self._role_map = role_map
        self._default = default

    def check(self, user_id: str | None, roles: Sequence[str] = ()) -> TrustLevel:
        """Return the TrustLevel for the given user_id and optional roles.

        Args:
            user_id: Platform user identifier, or None for anonymous/service messages.
            roles: Role names (e.g. Discord guild roles) for role-based lookup.

        Returns:
            The resolved TrustLevel.
        """
        if user_id is not None and user_id in self._user_map:
            return self._user_map[user_id]

        if roles:
            best: TrustLevel | None = None
            for role in roles:
                if role in self._role_map:
                    candidate = self._role_map[role]
                    if best is None or _TRUST_ORDER[candidate] > _TRUST_ORDER[best]:
                        best = candidate
            if best is not None:
                return best

        return self._default

    @classmethod
    def from_config(cls, raw: dict, section: str) -> "AuthMiddleware":
        """Parse raw TOML config dict and build an AuthMiddleware instance.

        Args:
            raw: Top-level parsed TOML dict (may contain an "auth" key).
            section: Adapter section name, e.g. "telegram", "discord", "cli".

        Returns:
            AuthMiddleware instance.

        Raises:
            SystemExit: If a required section is missing or the default value is
                invalid. Missing section for "cli" never raises — returns OWNER
                middleware.
        """
        auth_block: dict = raw.get("auth", {})
        section_cfg: dict | None = auth_block.get(section)

        if section_cfg is None:
            if section == "cli":
                # CLI is always local/trusted — fixed OWNER, no config required.
                return cls(user_map={}, role_map={}, default=TrustLevel.OWNER)
            raise ValueError(
                f"Missing [auth.{section}] in lyra.toml"
                f" — auth config required for networked adapters"
            )

        # Validate default
        raw_default: str = section_cfg.get("default", "")
        try:
            default = TrustLevel(raw_default)
        except ValueError:
            valid = ", ".join(t.value for t in TrustLevel)
            raise ValueError(
                f"Invalid default '{raw_default}' in [auth.{section}]"
                f" — must be one of: {valid}"
            )

        user_map: dict[str, TrustLevel] = {}
        for uid in section_cfg.get("owner_users", []):
            user_map[str(uid)] = TrustLevel.OWNER
        for uid in section_cfg.get("trusted_users", []):
            # owner_users take precedence — do not downgrade
            user_map.setdefault(str(uid), TrustLevel.TRUSTED)

        # trusted_roles must contain Discord role snowflake IDs (numeric strings),
        # not display names. Example: trusted_roles = ["123456789012345678"]
        role_map: dict[str, TrustLevel] = {}
        for role in section_cfg.get("trusted_roles", []):
            role_map[str(role)] = TrustLevel.TRUSTED

        return cls(user_map=user_map, role_map=role_map, default=default)
