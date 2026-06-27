"""SSE stream token minting — interim #1992 mitigation."""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field


@dataclass
class StreamTokenRegistry:
    """Maps session_id → opaque stream token required for SSE subscribe."""

    _tokens: dict[str, str] = field(default_factory=dict)

    def mint(self, session_id: str) -> str:
        token = secrets.token_urlsafe(32)
        self._tokens[session_id] = token
        return token

    def verify(self, session_id: str, token: str | None) -> bool:
        if not token:
            return False
        expected = self._tokens.get(session_id)
        return expected is not None and secrets.compare_digest(expected, token)

    def revoke(self, session_id: str) -> None:
        self._tokens.pop(session_id, None)