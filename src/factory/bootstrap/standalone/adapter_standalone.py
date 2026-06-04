"""Standalone adapter bootstrap — NATS-connected adapter without local Hub."""

from __future__ import annotations

import asyncio
import logging
import os
import sys

import nats.errors

from factory.bootstrap.factory.config import build_adapter_config_bundle
from factory.core.messaging.message import Platform
from factory.core.messaging.utils.metrics import log_contracts_version
from factory.paths import factory_data_dir
from roxabi_nats import nats_connect
from roxabi_nats.connect import scrub_nats_url

log = logging.getLogger(__name__)


async def _bootstrap_adapter_standalone(  # noqa: PLR0915, C901 — DEBT:migration-sequence-bootstrap
    raw_config: dict,
    platform: str,
    *,
    _stop: asyncio.Event | None = None,
) -> None:
    """Bootstrap a standalone adapter process connected to NATS.

    Args:
        raw_config: Parsed config dict (lyra config.toml content).
        platform: "telegram" or "discord".
        _stop: Optional event for graceful shutdown (tests inject this).
    """
    nats_url = os.environ.get("NATS_URL")
    if not nats_url:
        sys.exit("NATS_URL required for standalone adapter mode")

    log_contracts_version()

    try:
        platform_enum = Platform(platform)
    except ValueError:
        sys.exit(f"Unknown platform: {platform!r}")
    config_bundle = build_adapter_config_bundle(raw_config)

    try:
        nc = await nats_connect(nats_url, identity_name=f"{platform}-adapter")
        log.info(
            "adapter_standalone: connected to NATS at %s",
            scrub_nats_url(nats_url),
        )
    except (nats.errors.Error, OSError) as exc:
        sys.exit(f"Failed to connect to NATS at {scrub_nats_url(nats_url)!r}: {exc}")

    try:
        if platform == "telegram":
            from factory.bootstrap.wiring.standalone_telegram import (
                bootstrap_telegram_standalone,
            )

            await bootstrap_telegram_standalone(
                nc,
                raw_config,
                config_bundle,
                platform_enum,
                _stop=_stop,
            )
        elif platform == "discord":
            from factory.bootstrap.wiring.standalone_discord import (
                bootstrap_discord_standalone,
            )

            # Discord needs the factory data dir parent to exist so its private
            # named volume mount-point is reachable.  Telegram is stateless
            # post-#1721 and its container has a read-only ~/.roxabi — do NOT
            # mkdir for telegram (#1734).
            vault_dir = factory_data_dir()
            vault_dir.mkdir(parents=True, exist_ok=True)

            await bootstrap_discord_standalone(
                nc,
                raw_config,
                config_bundle,
                vault_dir,
                platform_enum,
                _stop=_stop,
            )
        else:
            sys.exit(f"Unknown platform: {platform!r}")
    finally:
        await nc.close()
