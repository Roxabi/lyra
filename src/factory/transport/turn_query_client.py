"""TurnQueryClient — hub-side NATS request/reply for TurnStore reads (#2309).

Implements the same read surface as TurnStoreProtocol + get_resume_count so
drivers, catalog, and ResumePublisherPort can drop the RO turns.db mount.
Fail-soft on timeout / no-responders (null / [] / 0) — match missing-store UX.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

import nats.errors
from nats.errors import NoRespondersError

from roxabi_contracts.turns import SUBJECTS

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

log = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 3.0


class TurnQueryClient:
    """Core NATS client for turn-writer query subjects."""

    def __init__(self, nc: "NATS", *, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self._nc = nc
        self._timeout = timeout

    async def close(self) -> None:
        """No-op — no SQLite handle (open_stores finally calls close)."""

    async def _request(
        self, subject: str, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        try:
            msg = await self._nc.request(
                subject,
                json.dumps(payload).encode(),
                timeout=self._timeout,
            )
        except (TimeoutError, asyncio.TimeoutError, NoRespondersError) as exc:
            log.warning(
                "turn-query client %s failed: %s",
                subject,
                type(exc).__name__,
            )
            return None
        except nats.errors.Error as exc:
            log.warning("turn-query client %s nats error: %s", subject, exc)
            return None
        try:
            data = json.loads(msg.data.decode()) if msg.data else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            log.warning("turn-query client %s invalid json reply", subject)
            return None
        if not isinstance(data, dict):
            return None
        if "error" in data:
            log.warning(
                "turn-query client %s error reply: %s", subject, data.get("error")
            )
            return None
        return data

    async def get_cli_session(self, session_id: str) -> str | None:
        data = await self._request(SUBJECTS.get_cli_session, {"session_id": session_id})
        if data is None:
            return None
        val = data.get("cli_session_id")
        return str(val) if val is not None else None

    async def get_cli_session_by_pool(self, pool_id: str) -> str | None:
        data = await self._request(
            SUBJECTS.get_cli_session_by_pool, {"pool_id": pool_id}
        )
        if data is None:
            return None
        val = data.get("cli_session_id")
        return str(val) if val is not None else None

    async def get_resume_count(self, session_id: str) -> int:
        data = await self._request(
            SUBJECTS.get_resume_count, {"session_id": session_id}
        )
        if data is None:
            return 0
        try:
            return int(data.get("resume_count", 0))
        except (TypeError, ValueError):
            return 0

    async def get_last_session(self, pool_id: str) -> str | None:
        data = await self._request(SUBJECTS.get_last_session, {"pool_id": pool_id})
        if data is None:
            return None
        val = data.get("session_id")
        return str(val) if val is not None else None

    async def list_sessions(self, pool_id: str, limit: int = 5) -> list[dict[str, Any]]:
        data = await self._request(
            SUBJECTS.list_sessions, {"pool_id": pool_id, "limit": limit}
        )
        if data is None:
            return []
        rows = data.get("sessions")
        return list(rows) if isinstance(rows, list) else []

    async def list_recent_sessions(self, limit: int = 200) -> list[dict[str, Any]]:
        data = await self._request(SUBJECTS.list_recent_sessions, {"limit": limit})
        if data is None:
            return []
        rows = data.get("sessions")
        return list(rows) if isinstance(rows, list) else []

    async def get_turns(
        self, pool_id: str, user_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        data = await self._request(
            SUBJECTS.get_turns,
            {"pool_id": pool_id, "user_id": user_id, "limit": limit},
        )
        if data is None:
            return []
        rows = data.get("turns")
        return list(rows) if isinstance(rows, list) else []

    async def get_turns_by_session(
        self, session_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        data = await self._request(
            SUBJECTS.get_turns_by_session,
            {"session_id": session_id, "limit": limit},
        )
        if data is None:
            return []
        rows = data.get("turns")
        return list(rows) if isinstance(rows, list) else []
