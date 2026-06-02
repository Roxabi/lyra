"""Helpers for hub_standalone — agent-config loading, pairing init, shutdown."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from factory.bootstrap.factory.config import (
    _build_agent_overrides,
    _load_pairing_config,
)
from factory.bootstrap.lifecycle.lifecycle_helpers import (
    teardown_buses,
    teardown_dispatchers,
)
from factory.core.agent.agent_loader import agent_row_to_config
from factory.infrastructure.stores.pairing import PairingManager, set_pairing_manager

if TYPE_CHECKING:
    from factory.adapters.nats.mint_failure_subscriber import MintFailureSubscriber
    from factory.core.agent import Agent
    from factory.core.hub.hub import Hub
    from factory.infrastructure.stores.agent_store import AgentStore
    from factory.infrastructure.stores.auth_store import AuthStore
    from factory.llm.llm_client import LlmClient

log = logging.getLogger(__name__)


async def start_mint_failure_subscriber(nc: Any) -> "MintFailureSubscriber | None":
    """Start the opt-in GitHub-App mint-failure NATS subscriber (#1078).

    Returns the started subscriber, or None if FACTORY_GH_OPS_TELEGRAM_BOT_ID +
    FACTORY_GH_OPS_TELEGRAM_CHAT_ID are not both set, or if startup fails.
    """
    from factory.adapters.nats.mint_failure_subscriber import MintFailureSubscriber

    bot_id = os.environ.get("FACTORY_GH_OPS_TELEGRAM_BOT_ID", "")
    chat_id_raw = os.environ.get("FACTORY_GH_OPS_TELEGRAM_CHAT_ID", "")
    if not (bot_id and chat_id_raw):
        log.info(
            "MintFailureSubscriber disabled"
            " — set FACTORY_GH_OPS_TELEGRAM_BOT_ID + FACTORY_GH_OPS_TELEGRAM_CHAT_ID"
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
    except Exception as exc:  # noqa: BLE001 — DEBT:boundary-broad-catch — opt-in subscriber: NATS subscribe or int() parse of chat_id may raise; failure is non-fatal, hub starts without it
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
    cli_nats_driver: "LlmClient | None",
    nats_llm_client: "LlmClient | None",
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
    if nats_llm_client is not None:
        await nats_llm_client.stop()
    await hub.shutdown()


def _build_active_list(tg_bot_auths: list[Any], dc_bot_auths: list[Any]) -> list[str]:
    """Return the list of active bot identifiers for logging."""
    return [f"telegram:{c.bot_id}" for c, _ in tg_bot_auths] + [
        f"discord:{c.bot_id}" for c, _ in dc_bot_auths
    ]


def _create_hub_tasks(hub: Any, health_server: Any) -> list[asyncio.Task[Any]]:
    """Create the core runtime tasks (hub + health) and optional audit consumer."""
    tasks: list[asyncio.Task[Any]] = [
        asyncio.create_task(hub.run(), name="hub"),
        asyncio.create_task(health_server.serve(), name="health"),
    ]
    if hub._event_bus is not None:
        from factory.core.hub.pipeline.audit_consumer import AuditConsumer

        _audit_queue = hub._event_bus.subscribe()
        _audit_consumer = AuditConsumer(_audit_queue)
        tasks.append(asyncio.create_task(_audit_consumer.run(), name="audit-consumer"))
    return tasks
