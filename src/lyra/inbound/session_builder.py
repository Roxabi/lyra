"""SessionBuilder — injects session_id and session_update_fn into InboundMessage."""

from __future__ import annotations

import dataclasses
import logging
import sqlite3
from typing import TYPE_CHECKING

from lyra.core.hub.hub_protocol import RoutingKey
from lyra.core.messaging.message import DiscordMeta, Platform
from lyra.core.stores.thread_store_protocol import ThreadSession

if TYPE_CHECKING:
    from lyra.core.messaging.message import InboundMessage
    from lyra.inbound.context import SessionCtx

log = logging.getLogger("lyra.inbound.session_builder")


class SessionBuilder:
    """Builds session context for an inbound message.

    Reads ``SessionCtx.turn_store``, ``SessionCtx.thread_store``, and
    ``SessionCtx.thread_sessions_cache`` to resolve or create a session.
    Returns an updated ``InboundMessage`` with ``session_update_fn`` populated.

    Handles four cases without importing platform libraries:

    (a) Both None  — test/CLI: no session persistence, return msg unchanged.
    (b) turn_store only — Telegram: DM/group via TurnStore.
    (c) Both, DM — Discord DM (guild_id=None, thread_id=None): turn_store only,
        same as (b).
    (d) Both, owned thread — Discord thread: ThreadStore read + write-through cache.

    The ``session_update_fn`` closure captures stores and IDs by reference and is
    safe to call after ``build`` returns — the inbound turn handler invokes it once
    a session_id is assigned.

    Platform meta discrimination uses ``isinstance(meta, DiscordMeta)`` to distinguish
    Discord from Telegram/Generic; within Discord, ``meta.guild_id`` and
    ``meta.thread_id`` determine DM vs thread path.
    """

    async def build(self, msg: InboundMessage, ctx: SessionCtx) -> InboundMessage:
        """Resolve session and return *msg* enriched with session context.

        Returns the original *msg* unchanged when both stores are None (path a).
        Otherwise returns a new ``InboundMessage`` via ``dataclasses.replace``
        with ``session_update_fn`` set and, when applicable, ``platform_meta``
        updated with the prior ``thread_session_id``.
        """
        ts = ctx.turn_store
        th = ctx.thread_store
        meta = msg.platform_meta

        # (a) No stores — test/CLI mode; nothing to inject.
        if ts is None and th is None:
            return msg

        # Discriminate path: Discord with thread_store present uses meta inspection.
        _discord_thread = (
            isinstance(meta, DiscordMeta)
            and th is not None
            and meta.thread_id is not None
        )
        if _discord_thread:
            # (d) Discord owned thread — ThreadStore read + write-through cache.
            return await self._build_thread_path(msg, ctx)

        # (b) / (c) TurnStore path — Telegram or Discord DM.
        # Require turn_store; if absent fall through to no-op (edge: discord + no ts).
        if ts is None:
            return msg
        return await self._build_turnstore_path(msg, ctx)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _build_turnstore_path(
        self, msg: InboundMessage, ctx: SessionCtx
    ) -> InboundMessage:
        """Path (b)/(c): inject prior session_id from TurnStore + persist closure."""
        ts = ctx.turn_store
        assert ts is not None  # guarded by caller

        meta = msg.platform_meta
        _platform = _platform_enum(msg.platform)
        _pool_id = RoutingKey(_platform, msg.bot_id, msg.scope_id).to_pool_id()

        _prior_session_id: str | None = None
        try:
            _prior_session_id = await ts.get_last_session(_pool_id)
        except (sqlite3.Error, RuntimeError):
            log.exception(
                "SessionBuilder: TurnStore.get_last_session failed pool_id=%s", _pool_id
            )

        # Capture by value so the closure is safe after build() returns.
        _ts = ts

        async def _turnstore_update_fn(
            _msg: InboundMessage, session_id: str, pool_id: str
        ) -> None:
            # Closure captures _ts and is called by the turn handler after a
            # session_id has been assigned.  Safe to call after build() returns.
            await _ts.start_session(session_id, pool_id)

        _replacements: dict = {"session_update_fn": _turnstore_update_fn}
        if _prior_session_id is not None and hasattr(meta, "thread_session_id"):
            _replacements["platform_meta"] = dataclasses.replace(
                meta, thread_session_id=_prior_session_id
            )
        return dataclasses.replace(msg, **_replacements)

    async def _build_thread_path(
        self, msg: InboundMessage, ctx: SessionCtx
    ) -> InboundMessage:
        """Path (d): Discord owned thread — cache/ThreadStore read + persist closure.

        Retrieves the prior session for this thread (cache-first, then ThreadStore).
        Falls back gracefully when ``retrieve_thread_session`` returns an unresolved
        ``ThreadSession`` (session_id=None) — the turn will start fresh with no
        prior context, and the update closure will still persist the new session_id.
        """
        th = ctx.thread_store
        assert th is not None  # guarded by caller

        meta = msg.platform_meta
        assert isinstance(meta, DiscordMeta)  # guarded by caller
        assert meta.thread_id is not None  # guarded by caller

        _thread_id_str = str(meta.thread_id)
        _cache: dict[str, ThreadSession] = ctx.thread_sessions_cache

        _stored: ThreadSession | None = None
        try:
            # Inline the retrieve logic to avoid importing discord_threads
            # (which imports discord) — see spec invariant.
            _cached = _cache.get(_thread_id_str)
            if _cached is not None:
                # LRU move-to-end
                _cache[_thread_id_str] = _cache.pop(_thread_id_str)
                _stored = _cached
            else:
                _ts_result = await th.get_session(
                    thread_id=_thread_id_str, bot_id=msg.bot_id
                )
                if _ts_result.is_resolved:
                    if len(_cache) >= 500:
                        _oldest = next(iter(_cache))
                        del _cache[_oldest]
                    _cache[_thread_id_str] = _ts_result
                _stored = _ts_result if _ts_result.is_resolved else None
        except (sqlite3.Error, RuntimeError):
            log.exception(
                "SessionBuilder: ThreadStore.get_session failed thread_id=%s",
                _thread_id_str,
            )
            _stored = None

        # Capture by value so the closure is safe after build() returns.
        _th = th
        _bid = msg.bot_id

        async def _thread_update_fn(
            _msg: InboundMessage, session_id: str, pool_id: str
        ) -> None:
            # Write-through: update ThreadStore and the in-memory cache.
            # Closure captures _th, _bid, _cache, _thread_id_str and is called
            # by the turn handler after a session_id has been assigned.
            # Safe to call after build() returns.
            _inner_meta = _msg.platform_meta
            _tid: int | None = (
                _inner_meta.thread_id if isinstance(_inner_meta, DiscordMeta) else None
            )
            if _tid is None:
                return
            _tid_str = str(_tid)
            try:
                await _th.update_session(
                    thread_id=_tid_str,
                    bot_id=_bid,
                    session_id=session_id,
                    pool_id=pool_id,
                )
                if len(_cache) >= 500:
                    _oldest_key = next(iter(_cache))
                    del _cache[_oldest_key]
                    log.debug(
                        "SessionBuilder: evicted thread_sessions cache entry"
                        " thread_id=%s (cache full)",
                        _oldest_key,
                    )
                _cache.pop(_tid_str, None)
                _cache[_tid_str] = ThreadSession(session_id=session_id, pool_id=pool_id)
            except (sqlite3.Error, RuntimeError):
                log.exception(
                    "SessionBuilder: ThreadStore.update_session failed thread_id=%s",
                    _tid_str,
                )

        _replacements: dict = {"session_update_fn": _thread_update_fn}
        if _stored is not None and _stored.session_id is not None:
            _replacements["platform_meta"] = dataclasses.replace(
                meta, thread_session_id=_stored.session_id
            )
        return dataclasses.replace(msg, **_replacements)


def _platform_enum(platform: str) -> Platform:
    """Map adapter platform string to ``Platform`` enum.

    Falls back to ``Platform.TELEGRAM`` only to keep the function total;
    unknown values are logged so callers can diagnose misconfiguration.
    """
    try:
        return Platform(platform)
    except ValueError:
        log.warning(
            "SessionBuilder: unknown platform %r, defaulting to TELEGRAM", platform
        )
        return Platform.TELEGRAM
