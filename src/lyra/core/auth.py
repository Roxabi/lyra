from __future__ import annotations

import logging
from enum import Enum

log = logging.getLogger(__name__)


class TrustLevel(Enum):
    """Authorization trust levels for inbound messages.

    OWNER   — local/CLI or explicitly designated owner user. Full access.
    TRUSTED — explicitly listed user. Full access (same as OWNER for now).
    PUBLIC  — currently synonymous with TRUSTED: passes through to the hub
              with no additional restrictions. Reserved for future capability-
              gating (e.g. rate-limits, read-only commands).
              # TODO: differentiate PUBLIC from TRUSTED once #commands lands.
    BLOCKED — rejected at the adapter layer before _normalize(). No LLM call.
    """

    OWNER = "owner"
    TRUSTED = "trusted"
    PUBLIC = "public"
    BLOCKED = "blocked"


class AuthMiddleware:
    def __init__(self, trust_map: dict[str, TrustLevel], default: TrustLevel) -> None:
        self._trust_map = trust_map
        self._default = default

    def check(self, user_id: str | None) -> TrustLevel:
        if user_id is None:
            return self._default
        return self._trust_map.get(user_id, self._default)

    @classmethod
    def from_config(cls, config: dict, section: str) -> "AuthMiddleware":
        """Parse config["auth"][section] -> AuthMiddleware.

        Fails closed (SystemExit) for networked sections if absent or malformed.
        Returns fixed-OWNER middleware for "cli" section if absent.
        """
        auth_root = config.get("auth", {})
        section_cfg = auth_root.get(section)
        if section_cfg is None:
            if section == "cli":
                return cls(trust_map={}, default=TrustLevel.OWNER)
            raise SystemExit(
                f"Missing required [auth.{section}] section in lyra.toml. "
                "Service refuses to start without auth config."
            )
        raw_default = section_cfg.get("default")
        if raw_default is None:
            raise SystemExit(f"[auth.{section}] missing 'default' field in lyra.toml")
        try:
            default = TrustLevel(raw_default)
        except ValueError:
            valid = [t.value for t in TrustLevel]
            raise SystemExit(
                f"[auth.{section}] 'default' value {raw_default!r} is not a valid "
                f"TrustLevel. Valid values: {valid}"
            )
        trust_map: dict[str, TrustLevel] = {}
        for uid in section_cfg.get("owner_users", []):
            trust_map[uid] = TrustLevel.OWNER
        for uid in section_cfg.get("trusted_users", []):
            trust_map.setdefault(uid, TrustLevel.TRUSTED)
        return cls(trust_map=trust_map, default=default)
