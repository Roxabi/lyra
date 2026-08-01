"""TurnQueryServer — core NATS request/reply reads over TurnStore (#2309).

Hub (and other bus clients) call factory.turns.get_* / list_* / get_turns* via
request/reply. Sole SQLite owner remains turn-writer; JetStream writes stay on
factory.turns.write (TurnWriter).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from roxabi_contracts.turns import SUBJECTS

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS
    from nats.aio.msg import Msg
    from nats.aio.subscription import Subscription

    from factory.infrastructure.stores.session.turn_store import TurnStore

log = logging.getLogger(__name__)

_DEFAULT_LIST_LIMIT = 5
_DEFAULT_TURNS_LIMIT = 50
_DEFAULT_RECENT_LIMIT = 200


class TurnQueryServer:
    """Subscribe to turn query subjects and reply with TurnStore rows."""

    def __init__(self, store: "TurnStore", nc: "NATS") -> None:
        self._store = store
        self._nc = nc
        self._subs: list["Subscription"] = []

    async def start(self) -> None:
        """Subscribe to all query subjects (idempotent if already started)."""
        if self._subs:
            return
        handlers: dict[str, Callable[["Msg"], Awaitable[None]]] = {
            SUBJECTS.get_cli_session: self._on_get_cli_session,
            SUBJECTS.get_cli_session_by_pool: self._on_get_cli_session_by_pool,
            SUBJECTS.get_resume_count: self._on_get_resume_count,
            SUBJECTS.get_last_session: self._on_get_last_session,
            SUBJECTS.list_sessions: self._on_list_sessions,
            SUBJECTS.list_recent_sessions: self._on_list_recent_sessions,
            SUBJECTS.get_turns: self._on_get_turns,
            SUBJECTS.get_turns_by_session: self._on_get_turns_by_session,
        }
        for subject, handler in handlers.items():
            sub = await self._nc.subscribe(subject, cb=handler)
            self._subs.append(sub)
        log.info("turn-query: subscribed to %d subjects", len(self._subs))

    async def stop(self) -> None:
        """Unsubscribe all query handlers."""
        for sub in self._subs:
            try:
                await sub.unsubscribe()
            except Exception:  # noqa: BLE001 — DEBT:boundary-broad-catch
                log.exception("turn-query: unsubscribe failed")
        self._subs.clear()
        log.info("turn-query: stopped")

    async def _respond(self, msg: "Msg", payload: dict[str, Any]) -> None:
        if not msg.reply:
            log.warning("turn-query: no reply subject subject=%s", msg.subject)
            return
        await msg.respond(json.dumps(payload, default=str).encode())

    async def _error(self, msg: "Msg", code: str, message: str) -> None:
        await self._respond(msg, {"error": {"code": code, "message": message}})

    def _parse(self, msg: "Msg") -> dict[str, Any] | None:
        if not msg.data:
            return {}
        try:
            raw = json.loads(msg.data.decode())
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
        return raw

    async def _on_get_cli_session(self, msg: "Msg") -> None:
        body = self._parse(msg)
        if body is None or "session_id" not in body:
            await self._error(msg, "bad_request", "session_id required")
            return
        try:
            cli = await self._store.get_cli_session(str(body["session_id"]))
            await self._respond(msg, {"cli_session_id": cli})
        except Exception:  # noqa: BLE001 — DEBT:boundary-broad-catch
            log.exception("turn-query get_cli_session failed")
            await self._error(msg, "internal_error", "get_cli_session failed")

    async def _on_get_cli_session_by_pool(self, msg: "Msg") -> None:
        body = self._parse(msg)
        if body is None or "pool_id" not in body:
            await self._error(msg, "bad_request", "pool_id required")
            return
        try:
            cli = await self._store.get_cli_session_by_pool(str(body["pool_id"]))
            await self._respond(msg, {"cli_session_id": cli})
        except Exception:  # noqa: BLE001 — DEBT:boundary-broad-catch
            log.exception("turn-query get_cli_session_by_pool failed")
            await self._error(msg, "internal_error", "get_cli_session_by_pool failed")

    async def _on_get_resume_count(self, msg: "Msg") -> None:
        body = self._parse(msg)
        if body is None or "session_id" not in body:
            await self._error(msg, "bad_request", "session_id required")
            return
        try:
            count = await self._store.get_resume_count(str(body["session_id"]))
            await self._respond(msg, {"resume_count": int(count)})
        except Exception:  # noqa: BLE001 — DEBT:boundary-broad-catch
            log.exception("turn-query get_resume_count failed")
            await self._error(msg, "internal_error", "get_resume_count failed")

    async def _on_get_last_session(self, msg: "Msg") -> None:
        body = self._parse(msg)
        if body is None or "pool_id" not in body:
            await self._error(msg, "bad_request", "pool_id required")
            return
        try:
            sid = await self._store.get_last_session(str(body["pool_id"]))
            await self._respond(msg, {"session_id": sid})
        except Exception:  # noqa: BLE001 — DEBT:boundary-broad-catch
            log.exception("turn-query get_last_session failed")
            await self._error(msg, "internal_error", "get_last_session failed")

    async def _on_list_sessions(self, msg: "Msg") -> None:
        body = self._parse(msg)
        if body is None or "pool_id" not in body:
            await self._error(msg, "bad_request", "pool_id required")
            return
        limit = int(body.get("limit", _DEFAULT_LIST_LIMIT))
        try:
            rows = await self._store.list_sessions(str(body["pool_id"]), limit)
            await self._respond(msg, {"sessions": list(rows)})
        except Exception:  # noqa: BLE001 — DEBT:boundary-broad-catch
            log.exception("turn-query list_sessions failed")
            await self._error(msg, "internal_error", "list_sessions failed")

    async def _on_list_recent_sessions(self, msg: "Msg") -> None:
        body = self._parse(msg)
        if body is None:
            await self._error(msg, "bad_request", "invalid json")
            return
        limit = int(body.get("limit", _DEFAULT_RECENT_LIMIT))
        try:
            rows = await self._store.list_recent_sessions(limit)
            await self._respond(msg, {"sessions": list(rows)})
        except Exception:  # noqa: BLE001 — DEBT:boundary-broad-catch
            log.exception("turn-query list_recent_sessions failed")
            await self._error(msg, "internal_error", "list_recent_sessions failed")

    async def _on_get_turns(self, msg: "Msg") -> None:
        body = self._parse(msg)
        if body is None or "pool_id" not in body or "user_id" not in body:
            await self._error(msg, "bad_request", "pool_id and user_id required")
            return
        limit = int(body.get("limit", _DEFAULT_TURNS_LIMIT))
        try:
            rows = await self._store.get_turns(
                str(body["pool_id"]), str(body["user_id"]), limit
            )
            await self._respond(msg, {"turns": list(rows)})
        except Exception:  # noqa: BLE001 — DEBT:boundary-broad-catch
            log.exception("turn-query get_turns failed")
            await self._error(msg, "internal_error", "get_turns failed")

    async def _on_get_turns_by_session(self, msg: "Msg") -> None:
        body = self._parse(msg)
        if body is None or "session_id" not in body:
            await self._error(msg, "bad_request", "session_id required")
            return
        limit = int(body.get("limit", _DEFAULT_TURNS_LIMIT))
        try:
            rows = await self._store.get_turns_by_session(
                str(body["session_id"]), limit
            )
            await self._respond(msg, {"turns": list(rows)})
        except Exception:  # noqa: BLE001 — DEBT:boundary-broad-catch
            log.exception("turn-query get_turns_by_session failed")
            await self._error(msg, "internal_error", "get_turns_by_session failed")
