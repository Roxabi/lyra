"""Active-jobs read layer for dashboard BFF (#1772)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from factory.core.hub.session_catalog import agent_for_pool, parse_pool_id
from factory.core.ports.active_jobs import ActiveJobEntry

if TYPE_CHECKING:
    from factory.core.hub import Hub


def entry_to_row(entry: ActiveJobEntry, bindings: dict[Any, Any]) -> dict[str, Any]:
    """Map registry entry to dashboard wire dict."""
    platform, _bot, _scope = parse_pool_id(entry.pool_id)
    return {
        "job_id": entry.job_id,
        "pool_id": entry.pool_id,
        "agent": agent_for_pool(entry.pool_id, bindings),
        "platform": platform or None,
        "status": entry.status,
        "started_at": entry.started_at.isoformat(),
        "concurrency_mode": entry.concurrency_mode,
        "worker_loc": entry.worker_loc,
        "steer_subject": entry.steer_subject,
    }


async def list_active_jobs(hub: Hub) -> list[dict[str, Any]]:
    """Return active jobs from coordinator snapshot or KV scan."""
    entries: list[ActiveJobEntry] = []
    coord = getattr(hub, "_active_jobs_coord", None)
    if coord is not None and hasattr(coord, "snapshot"):
        entries = coord.snapshot()
    if not entries:
        store = getattr(hub, "_active_jobs_store", None)
        if store is not None and hasattr(store, "list_all"):
            entries = await store.list_all()
    rows = [entry_to_row(e, hub.bindings) for e in entries]
    rows.sort(key=lambda r: r["started_at"], reverse=True)
    return rows