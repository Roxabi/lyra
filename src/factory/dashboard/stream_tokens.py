"""SSE stream token minting — multi-slot registry (#2316).

Tokens are opaque secrets mapped to a stream key (chat session_id, or a
kind label like ``jobs`` / ``pipeline``). Multiple concurrent mints for the
same stream key remain valid until each token is revoked — operators no
longer clobber each other when opening parallel jobs/pipeline SSE feeds.

Chat still calls :meth:`revoke` with the session_id on stream end (clears
every token for that session). Jobs/pipeline SSE must call
:meth:`revoke_token` so peer streams on the same kind stay alive.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field


@dataclass
class StreamTokenRegistry:
    """Maps opaque stream tokens → stream key (multi-slot per key)."""

    _by_token: dict[str, str] = field(default_factory=dict)
    _by_stream: dict[str, set[str]] = field(default_factory=dict)

    def mint(self, session_id: str) -> str:
        """Issue a new token bound to *session_id* (does not invalidate peers)."""
        token = secrets.token_urlsafe(32)
        self._by_token[token] = session_id
        self._by_stream.setdefault(session_id, set()).add(token)
        return token

    def verify(self, session_id: str, token: str | None) -> bool:
        if not token:
            return False
        bound = self._by_token.get(token)
        if bound is None:
            return False
        # compare_digest requires equal-length strings; stream keys are short
        # fixed labels / session hex — length mismatch ⇒ reject without raise.
        if len(bound) != len(session_id):
            return False
        return secrets.compare_digest(bound, session_id)

    def revoke(self, session_id: str) -> None:
        """Revoke every token bound to *session_id* (chat session close)."""
        tokens = self._by_stream.pop(session_id, set())
        for tok in tokens:
            self._by_token.pop(tok, None)

    def revoke_token(self, token: str | None) -> None:
        """Revoke a single token without affecting peers on the same stream key."""
        if not token:
            return
        stream = self._by_token.pop(token, None)
        if stream is None:
            return
        bucket = self._by_stream.get(stream)
        if bucket is None:
            return
        bucket.discard(token)
        if not bucket:
            self._by_stream.pop(stream, None)
