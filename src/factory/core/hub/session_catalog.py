"""Agent-centric session catalog — read layer over TurnStore (#1771)."""

from __future__ import annotations

from factory.core.config.turn_store_config import TurnStoreConfig
from factory.core.hub.hub_protocol import Binding, RoutingKey
from factory.core.messaging.message import Platform
from factory.core.stores.turn_store_protocol import CatalogSessionRow, TurnStoreProtocol

__all__ = ["agent_for_pool", "list_sessions_for_agent", "parse_pool_id"]


def parse_pool_id(pool_id: str) -> tuple[str, str, str]:
    """Split pool_id into (platform, bot_id, scope_id). Scope may contain colons."""
    parts = pool_id.split(":", 2)
    if len(parts) < 3:
        return "", "", pool_id
    return parts[0], parts[1], parts[2]


def agent_for_pool(pool_id: str, bindings: dict[RoutingKey, Binding]) -> str | None:
    """Resolve agent_name for a concrete pool_id using exact then wildcard bindings."""
    platform_s, bot_id, scope_id = parse_pool_id(pool_id)
    if not platform_s:
        return None
    try:
        platform = Platform(platform_s)
    except ValueError:
        return None
    exact = bindings.get(RoutingKey(platform, bot_id, scope_id))
    if exact is not None:
        return exact.agent_name
    wildcard = bindings.get(RoutingKey(platform, bot_id, "*"))
    if wildcard is not None:
        return wildcard.agent_name
    return None


async def list_sessions_for_agent(
    store: TurnStoreProtocol,
    bindings: dict[RoutingKey, Binding],
    agent_name: str,
    *,
    limit: int = TurnStoreConfig.SESSION_CATALOG_DEFAULT_LIMIT,
    scan_limit: int = TurnStoreConfig.DEFAULT_LIST_RECENT_SESSIONS_LIMIT,
) -> list[CatalogSessionRow]:
    """Return recent sessions for *agent_name* across all platforms."""
    rows = await store.list_recent_sessions(scan_limit)
    filtered = [
        row
        for row in rows
        if agent_for_pool(row["pool_id"], bindings) == agent_name
    ]
    filtered.sort(key=lambda r: r["last_active_at"], reverse=True)
    return filtered[
        : max(1, min(limit, TurnStoreConfig.SESSION_CATALOG_MAX_LIMIT))
    ]