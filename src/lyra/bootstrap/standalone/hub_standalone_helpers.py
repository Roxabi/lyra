"""Helpers for hub_standalone — agent-config loading, pairing init, shutdown."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lyra.bootstrap.factory.config import _build_agent_overrides, _load_pairing_config
from lyra.bootstrap.lifecycle.lifecycle_helpers import (
    teardown_buses,
    teardown_dispatchers,
)
from lyra.core.agent.agent_loader import agent_row_to_config
from lyra.infrastructure.stores.pairing import PairingManager, set_pairing_manager

if TYPE_CHECKING:
    from lyra.adapters.nats.mint_failure_subscriber import MintFailureSubscriber
    from lyra.core.agent import Agent
    from lyra.core.hub.hub import Hub
    from lyra.infrastructure.stores.agent_store import AgentStore
    from lyra.infrastructure.stores.auth_store import AuthStore
    from lyra.llm.drivers.cli_nats import CliNatsDriver
    from lyra.llm.drivers.nats_driver import NatsLlmDriver

log = logging.getLogger(__name__)


async def start_mint_failure_subscriber(nc: Any) -> "MintFailureSubscriber | None":
    """Start the opt-in GitHub-App mint-failure NATS subscriber (#1078).

    Returns the started subscriber, or None if LYRA_GH_OPS_TELEGRAM_BOT_ID +
    LYRA_GH_OPS_TELEGRAM_CHAT_ID are not both set, or if startup fails.
    """
    from lyra.adapters.nats.mint_failure_subscriber import MintFailureSubscriber

    bot_id = os.environ.get("LYRA_GH_OPS_TELEGRAM_BOT_ID", "")
    chat_id_raw = os.environ.get("LYRA_GH_OPS_TELEGRAM_CHAT_ID", "")
    if not (bot_id and chat_id_raw):
        log.info(
            "MintFailureSubscriber disabled"
            " — set LYRA_GH_OPS_TELEGRAM_BOT_ID + LYRA_GH_OPS_TELEGRAM_CHAT_ID"
            " to enable"
        )
        return None
    try:
        sub = MintFailureSubscriber(
            nc,
            ops_telegram_bot_id=bot_id,
            ops_telegram_chat_id=int(chat_id_raw),
        )
        await sub.start()
    except Exception as exc:  # noqa: BLE001
        log.warning("MintFailureSubscriber failed to start: %s", exc)
        return None
    return sub


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
    cli_nats_driver: "CliNatsDriver | None",
    nats_llm_driver: "NatsLlmDriver | None",
) -> None:
    """Run the post-cancellation teardown sequence for hub_standalone."""
    await readiness_sub.unsubscribe()
    await teardown_buses(hub.inbound_bus)
    await teardown_dispatchers(dispatchers)
    for proxy in proxies:
        await proxy.publish_stream_errors("hub_shutdown")
    if pm is not None:
        await pm.close()
    if cli_nats_driver is not None:
        await cli_nats_driver.stop()
    if nats_llm_driver is not None:
        await nats_llm_driver.stop()
    await hub.shutdown()
