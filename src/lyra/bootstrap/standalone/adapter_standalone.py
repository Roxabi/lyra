"""Standalone adapter bootstrap — NATS-connected adapter without local Hub."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from lyra.bootstrap.factory.config import build_adapter_config_bundle
from lyra.core.messaging.message import Platform
from lyra.core.messaging.metrics import log_contracts_version
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

    platform_enum = Platform(platform)
    config_bundle = build_adapter_config_bundle(raw_config)

    try:
        nc = await nats_connect(nats_url, identity_name=f"{platform}-adapter")
        log.info(
            "adapter_standalone: connected to NATS at %s",
            scrub_nats_url(nats_url),
        )
    except Exception as exc:  # noqa: BLE001 — DEBT:boundary-broad-catch
        sys.exit(f"Failed to connect to NATS at {scrub_nats_url(nats_url)!r}: {exc}")

    vault_dir = Path(os.environ.get("LYRA_VAULT_DIR", str(Path.home() / ".lyra")))
    vault_dir.mkdir(parents=True, exist_ok=True)

    try:
        if platform == "telegram":
            from lyra.bootstrap.wiring.standalone_telegram import (
                bootstrap_telegram_standalone,
            )

            await bootstrap_telegram_standalone(
                nc,
                raw_config,
                config_bundle,
                vault_dir,
                platform_enum,
                _stop=_stop,
            )
        elif platform == "discord":
            from lyra.bootstrap.wiring.standalone_discord import (
                bootstrap_discord_standalone,
            )

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
