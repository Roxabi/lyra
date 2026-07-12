"""Open control-plane identity store for factory-dashboard (ADR-103)."""

from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

from factory.infrastructure.stores.identity.control_plane_store import ControlPlaneStore
from factory.paths import factory_data_dir

log = logging.getLogger(__name__)

__all__ = ["open_control_plane_store"]


def _auth_db_path() -> Path:
    override = os.environ.get("FACTORY_AUTH_DB", "").strip()
    if override:
        return Path(override)
    return factory_data_dir() / "auth.db"


async def open_control_plane_store(
    *,
    db_path: Path | None = None,
) -> ControlPlaneStore:
    """Connect ControlPlaneStore and ensure a bootstrap admin when configured.

    Bootstrap env (optional):
    * ``FACTORY_DASHBOARD_BOOTSTRAP_ADMIN_EMAIL``
    * ``FACTORY_DASHBOARD_BOOTSTRAP_ADMIN_PASSWORD`` — if email set and password
      empty, a one-time random password is generated and logged once (dev only).
    """
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
