"""SQLite control-plane identity store composition root (ADR-103)."""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

from factory.infrastructure.stores.base.sqlite_base import SqliteStore
from factory.infrastructure.stores.identity.control_plane_ddl import (
    API_KEY_PREFIX,
    DDL_CONTROL_PLANE,
    SESSION_COOKIE_NAME,
)
from factory.infrastructure.stores.identity.control_plane_invites import (
    ControlPlaneInviteOps,
)
from factory.infrastructure.stores.identity.control_plane_links import (
    ControlPlaneLinkOps,
)
from factory.infrastructure.stores.identity.control_plane_orgs import (
    ControlPlaneJobMetaOps,
    ControlPlaneOrgOps,
)
from factory.infrastructure.stores.identity.control_plane_sessions import (
    ControlPlaneSessionOps,
)
from factory.infrastructure.stores.identity.control_plane_users import (
    ControlPlaneUserOps,
)
from factory.infrastructure.stores.identity.user_store import _CREATE_USERS_EMAIL_INDEX
from factory.paths import factory_data_dir

log = logging.getLogger(__name__)

__all__ = [
    "API_KEY_PREFIX",
    "ControlPlaneStore",
    "DDL_CONTROL_PLANE",
    "SESSION_COOKIE_NAME",
    "open_control_plane_store",
]


class ControlPlaneStore(
    ControlPlaneUserOps,
    ControlPlaneInviteOps,
    ControlPlaneSessionOps,
    ControlPlaneOrgOps,
    ControlPlaneJobMetaOps,
    ControlPlaneLinkOps,
    SqliteStore,
):
    """Dashboard users, invites, sessions, API keys on auth.db (or dedicated path)."""

    def __init__(self, db_path: str | Path) -> None:
        super().__init__(db_path)

    async def connect(self) -> None:
        await self._open_db(ddl=list(DDL_CONTROL_PLANE))
        await self._migrate_user_auth_columns()
        db = self._require_db()
        await db.execute(_CREATE_USERS_EMAIL_INDEX)
        await db.commit()
        log.info("ControlPlaneStore connected (db=%s)", self._db_path)

    async def _migrate_user_auth_columns(self) -> None:
        db = self._require_db()
        async with db.execute("PRAGMA table_info(users)") as cur:
            cols = {row[1] async for row in cur}
        alters: list[str] = []
        if "password_hash" not in cols:
            alters.append("ALTER TABLE users ADD COLUMN password_hash TEXT")
        if "global_role" not in cols:
            alters.append(
                "ALTER TABLE users ADD COLUMN global_role "
                "TEXT NOT NULL DEFAULT 'member'"
            )
        if "status" not in cols:
            alters.append(
                "ALTER TABLE users ADD COLUMN status "
                "TEXT NOT NULL DEFAULT 'active'"
            )
        for stmt in alters:
            await db.execute(stmt)
        if alters:
            await db.commit()
            log.info("ControlPlaneStore migrated users columns: %s", alters)


def _auth_db_path() -> Path:
    override = os.environ.get("FACTORY_AUTH_DB", "").strip()
    if override:
        return Path(override)
    return factory_data_dir() / "auth.db"


async def open_control_plane_store(
    *,
    db_path: Path | None = None,
) -> ControlPlaneStore:
    """Connect ControlPlaneStore and ensure a bootstrap admin when configured."""
    path = db_path or _auth_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    store = ControlPlaneStore(path)
    await store.connect()

    email = os.environ.get("FACTORY_DASHBOARD_BOOTSTRAP_ADMIN_EMAIL", "").strip()
    password = os.environ.get("FACTORY_DASHBOARD_BOOTSTRAP_ADMIN_PASSWORD", "").strip()
    if email:
        if not password:
            password = secrets.token_urlsafe(18)
            log.warning(
                "Bootstrap admin password generated for %s (set "
                "FACTORY_DASHBOARD_BOOTSTRAP_ADMIN_PASSWORD to pin it): %s",
                email,
                password,
            )
        created = await store.bootstrap_admin_if_empty(
            email=email,
            password=password,
        )
        if created is None:
            log.info("Control-plane admin already present — bootstrap skipped")
    else:
        log.info(
            "No FACTORY_DASHBOARD_BOOTSTRAP_ADMIN_EMAIL — "
            "invite/bootstrap admin via ops when ready"
        )
    return store
