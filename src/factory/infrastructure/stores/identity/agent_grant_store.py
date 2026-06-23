"""AgentGrantStore: SQLite + write-through cache for agent-scoped authorization.

ADR-090 slice 1 (Foundation). Implements the ``AgentAuthorizer`` read port plus
the operator write surface (``grant`` / ``revoke``). Shares ``auth.db`` with
``AuthStore`` and the pairing/alias grants (ADR-090 §3). ``authorize`` is
synchronous — it reads only the warm in-memory cache so it never blocks the hub
event loop; writes are async and update the cache write-through, so grants take
effect without a hub restart.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from factory.core.auth.agent_grants import (
    AgentGrant,
    AuthDecision,
    Capability,
    Principal,
    PrincipalKind,
)
from factory.infrastructure.stores.base.sqlite_base import SqliteStore

log = logging.getLogger(__name__)

__all__ = ["AgentGrantStore"]


_CREATE_AGENT_GRANTS = """
CREATE TABLE IF NOT EXISTS agent_grants (
    id             INTEGER PRIMARY KEY,
    agent_name     TEXT NOT NULL,
    principal_kind TEXT NOT NULL,
    principal_id   TEXT NOT NULL,
    capability     TEXT NOT NULL,
    granted_by     TEXT NOT NULL,
    source         TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(agent_name, principal_kind, principal_id, capability)
)
"""

_SELECT_COLS = (
    "agent_name, principal_kind, principal_id, capability, "
    "granted_by, source, created_at"
)


def _make_grant(row: tuple[str, ...]) -> AgentGrant:
    """Build an AgentGrant from a raw ``agent_grants`` row (UTC-normalised).

    Column order matches ``_SELECT_COLS``.
    """
    agent_name, kind, principal_id, capability, granted_by, source, created_at = row
    ts = datetime.fromisoformat(created_at)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return AgentGrant(
        agent_name=agent_name,
        principal=Principal(kind=PrincipalKind(kind), id=principal_id),
        capability=Capability(capability),
        granted_by=granted_by,
        source=source,
        created_at=ts,
    )


class AgentGrantStore(SqliteStore):
    """SQLite-backed agent authorization matrix with write-through cache.

    The cache maps ``agent_name → list[AgentGrant]``. ``authorize`` and
    ``list_grants`` read only from the cache (sync, non-blocking); ``grant`` and
    ``revoke`` mutate the DB then reload the affected agent into the cache.
    Fail-safe: an agent with no matching ``use`` grant denies.
    """

    def __init__(self, db_path: str | Path) -> None:
        super().__init__(db_path)
        self._cache: dict[str, list[AgentGrant]] = {}

    async def connect(self) -> None:
        """Open aiosqlite, enable WAL, create agent_grants table, warm cache."""
        await self._open_db(ddl=[_CREATE_AGENT_GRANTS])
        await self._warm_cache()
        log.info("AgentGrantStore connected (db=%s)", self._db_path)

    async def _warm_cache(self) -> None:
        """Load every grant from the DB into the per-agent cache."""
        db = self._require_db()
        self._cache.clear()
        async with db.execute(f"SELECT {_SELECT_COLS} FROM agent_grants") as cur:
            async for row in cur:
                grant = _make_grant(tuple(row))
                self._cache.setdefault(grant.agent_name, []).append(grant)

    async def _reload_agent(self, agent_name: str) -> None:
        """Re-read a single agent's grants from the DB into the cache.

        Keeps the cache consistent with the DB after a write — picking up the
        SQLite-assigned ``created_at`` and any ``ON CONFLICT`` update — and drops
        the agent key entirely when its last grant is revoked.
        """
        db = self._require_db()
        grants: list[AgentGrant] = []
        async with db.execute(
            f"SELECT {_SELECT_COLS} FROM agent_grants WHERE agent_name = ?",
            (agent_name,),
        ) as cur:
            async for row in cur:
                grants.append(_make_grant(tuple(row)))
        if grants:
            self._cache[agent_name] = grants
        else:
            self._cache.pop(agent_name, None)

    def authorize(
        self,
        *,
        agent_name: str,
        user_id: str,
        roles: Sequence[str] = (),
    ) -> AuthDecision:
        """Allow iff the user or one of their roles holds a ``use`` grant.

        Matching is kind-aware: ``user_id`` matches only ``USER`` grants and
        ``roles`` match only ``ROLE`` grants, so the ``PrincipalKind`` recorded
        on each grant is honoured rather than treated as inert metadata.
        """
        grants = self._cache.get(agent_name)
        if not grants:
            return AuthDecision.deny(f"no grants for agent {agent_name!r}")
        role_set = set(roles)
        for grant in grants:
            if grant.capability is not Capability.USE:
                continue
            principal = grant.principal
            if principal.kind is PrincipalKind.USER and principal.id == user_id:
                return AuthDecision.allow(f"use granted to user {principal.id}")
            if principal.kind is PrincipalKind.ROLE and principal.id in role_set:
                return AuthDecision.allow(f"use granted via role {principal.id}")
        return AuthDecision.deny(f"no use grant for principals on agent {agent_name!r}")

    def list_grants(self, agent_name: str) -> tuple[AgentGrant, ...]:
        """Return all grants for *agent_name* from cache (sync, no I/O)."""
        return tuple(self._cache.get(agent_name, ()))

    async def grant(
        self,
        agent_name: str,
        principal: Principal,
        *,
        capability: Capability = Capability.USE,
        granted_by: str,
        source: str,
    ) -> AgentGrant:
        """Insert or update a grant in DB and cache; return the persisted row."""
        if not agent_name:
            raise ValueError("agent_name must be non-empty")
        db = self._require_db()
        await db.execute(
            "INSERT INTO agent_grants (agent_name, principal_kind, principal_id, "
            "capability, granted_by, source) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(agent_name, principal_kind, principal_id, capability) "
            "DO UPDATE SET granted_by=excluded.granted_by, source=excluded.source",
            (
                agent_name,
                principal.kind.value,
                principal.id,
                capability.value,
                granted_by,
                source,
            ),
        )
        await db.commit()
        await self._reload_agent(agent_name)
        for grant in self._cache.get(agent_name, ()):
            if grant.principal == principal and grant.capability is capability:
                return grant
        # Unreachable: the row was just committed and reloaded.
        raise RuntimeError(
            f"grant for {principal.id!r} on {agent_name!r} missing after write"
        )

    async def revoke(
        self,
        agent_name: str,
        principal: Principal,
        *,
        capability: Capability = Capability.USE,
    ) -> bool:
        """Delete a grant from DB and cache; return True if one existed."""
        db = self._require_db()
        async with db.execute(
            "DELETE FROM agent_grants WHERE agent_name = ? AND principal_kind = ? "
            "AND principal_id = ? AND capability = ?",
            (agent_name, principal.kind.value, principal.id, capability.value),
        ) as cur:
            deleted = cur.rowcount > 0
        await db.commit()
        await self._reload_agent(agent_name)
        return deleted

    async def close(self) -> None:
        """Close the database connection."""
        await super().close()
        log.info("AgentGrantStore closed")
