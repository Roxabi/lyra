"""SQLite store for connector installation registry (ingress.db)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from factory.infrastructure.stores.base.sqlite_base import (
    _SQLITE_STORE_ERRORS,
    SqliteStore,
)
from factory.infrastructure.stores.migrations.ingress_store_migrations import (
    CREATE_CONNECTOR_INSTALLATIONS,
    run_ingress_migrations,
)
from factory.ingress.connectors.base import validate_factory_tenant
from factory.ingress.ports import VerificationError

log = logging.getLogger(__name__)


class InstallationStore(SqliteStore):
    """Maps (connector, external_id) → factory_tenant (ADR-096)."""

    async def connect(self) -> None:
        if self._db is not None:
            return
        await self._open_db(ddl=[CREATE_CONNECTOR_INSTALLATIONS])
        try:
            db = self._require_db()
            await run_ingress_migrations(db)
        except _SQLITE_STORE_ERRORS:
            log.exception("InstallationStore.connect() setup failed; closing")
            await self.close()
            raise
        log.info("InstallationStore connected (db=%s)", self._db_path)

    async def resolve(self, connector: str, external_id: str | None) -> str | None:
        if external_id is not None:
            db = self._require_db()
            async with db.execute(
                "SELECT factory_tenant FROM connector_installations "
                "WHERE connector = ? AND external_id = ? AND enabled = 1",
                (connector, external_id),
            ) as cur:
                row = await cur.fetchone()
            if row is not None:
                return self._validated_tenant(
                    str(row[0]), connector=connector, external_id=external_id
                )
            return None
        if connector == "github":
            return "default"
        return None

    async def upsert_lifecycle(
        self,
        connector: str,
        external_id: str,
        factory_tenant: str,
        *,
        enabled: bool,
    ) -> None:
        if external_id == "*":
            raise ValueError("wildcard external_id forbidden")
        validate_factory_tenant(factory_tenant)
        now = datetime.now(tz=UTC).isoformat()
        db = self._require_db()
        await db.execute(
            "INSERT INTO connector_installations "
            "(connector, external_id, factory_tenant, enabled, "
            "metadata_json, updated_at) "
            "VALUES (?, ?, ?, ?, NULL, ?) "
            "ON CONFLICT(connector, external_id) DO UPDATE SET "
            "factory_tenant = excluded.factory_tenant, "
            "enabled = excluded.enabled, "
            "updated_at = excluded.updated_at",
            (connector, external_id, factory_tenant, 1 if enabled else 0, now),
        )
        await db.commit()

    async def seed(
        self,
        connector: str,
        external_id: str,
        factory_tenant: str,
    ) -> None:
        await self.upsert_lifecycle(
            connector, external_id, factory_tenant, enabled=True
        )

    async def list_rows(self) -> list[dict[str, str | bool]]:
        db = self._require_db()
        rows: list[dict[str, str | bool]] = []
        async with db.execute(
            "SELECT connector, external_id, factory_tenant, enabled "
            "FROM connector_installations ORDER BY connector, external_id"
        ) as cur:
            async for row in cur:
                rows.append(
                    {
                        "connector": str(row[0]),
                        "external_id": str(row[1]),
                        "factory_tenant": str(row[2]),
                        "enabled": bool(row[3]),
                    }
                )
        return rows

    @staticmethod
    def _validated_tenant(
        raw: str, *, connector: str, external_id: str
    ) -> str | None:
        try:
            return validate_factory_tenant(raw)
        except VerificationError:
            log.warning(
                "invalid_registry_tenant connector=%s external_id=%s slug=%s",
                connector,
                external_id,
                raw,
            )
            return None


def default_db_path() -> Path:
    import os

    raw = os.environ.get("INGRESS_DB_PATH", "").strip()
    if raw:
        return Path(raw)
    return Path.home() / ".roxabi" / "factory" / "ingress.db"