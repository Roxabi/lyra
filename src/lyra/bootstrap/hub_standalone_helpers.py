"""Helpers for hub_standalone — agent-config loading, pairing init, shutdown."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from lyra.bootstrap.config import _build_agent_overrides, _load_pairing_config
from lyra.bootstrap.lifecycle_helpers import teardown_buses, teardown_dispatchers
from lyra.core.agent.agent_loader import agent_row_to_config
from lyra.core.stores.pairing import PairingManager, set_pairing_manager

if TYPE_CHECKING:
    from lyra.core.agent import Agent
    from lyra.core.cli.cli_pool import CliPool
    from lyra.core.hub.hub import Hub
    from lyra.infrastructure.stores.agent_store import AgentStore
    from lyra.infrastructure.stores.auth_store import AuthStore

log = logging.getLogger(__name__)


def load_agent_configs(
    agent_store: AgentStore,
    raw_config: dict,
    agent_names: Iterable[str],
) -> dict[str, Agent]:
    """Load agent configs from DB with instance overrides applied."""
    configs: dict[str, Agent] = {}
    for n in sorted(set(agent_names)):
        row = agent_store.get(n)
        if row is None:
            log.error("Agent %r not found in DB — skipping", n)
            continue
        overrides = _build_agent_overrides(raw_config, n)
        configs[n] = agent_row_to_config(row, instance_overrides=overrides.model_dump())
    return configs


async def build_pairing_manager(
    raw_config: dict,
    *,
    vault_dir: Path,
    auth_store: AuthStore,
    admin_user_ids: Iterable[str],
) -> PairingManager | None:
    """Construct + connect PairingManager if enabled; None otherwise."""
    pairing_config = _load_pairing_config(raw_config)
    if pairing_config.enabled and not admin_user_ids:
        log.warning(
            "Pairing enabled but [admin].user_ids is empty — "
            "/invite and /unpair require is_admin=True "
            "(granted to [admin].user_ids entries "
            "or users configured as OWNER in [[auth.*_bots]])"
        )
    if not pairing_config.enabled:
        return None
    pm = PairingManager(
        config=pairing_config,
        db_path=vault_dir / "pairing.db",
        auth_store=auth_store,
    )
    await pm.connect()
    set_pairing_manager(pm)
    return pm


async def shutdown_hub_runtime(  # noqa: PLR0913 — unavoidable wiring surface
    hub: Hub,
    *,
    readiness_sub,
    dispatchers,
    proxies,
    pm: PairingManager | None,
    cli_pool: CliPool | None,
    nats_llm_driver,
) -> None:
    """Run the post-cancellation teardown sequence for hub_standalone."""
    await readiness_sub.unsubscribe()
    await teardown_buses(hub.inbound_bus)
    await teardown_dispatchers(dispatchers)
    for proxy in proxies:
        await proxy.publish_stream_errors("hub_shutdown")
    if pm is not None:
        await pm.close()
    if cli_pool is not None:
        await cli_pool.drain(timeout=60.0)
        active_ids = cli_pool.get_active_pool_ids()
        if active_ids:
            await hub.notify_shutdown_inflight(active_ids)
        await cli_pool.stop()
    if nats_llm_driver is not None:
        await nats_llm_driver.stop()
    await hub.shutdown()
