"""Standalone adapter bootstrap — NATS-connected adapter without local Hub."""

from __future__ import annotations

import asyncio
import logging
import os
import ssl
import sys
from typing import TYPE_CHECKING

import nats.errors

from factory.bootstrap.factory.config import build_adapter_config_bundle
from factory.core.messaging.message import Platform
from factory.core.messaging.utils.metrics import log_contracts_version
from roxabi_nats import nats_connect
from roxabi_nats.connect import scrub_nats_url

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

log = logging.getLogger(__name__)


async def _close_quietly(nc: NATS) -> None:
    """Close a NATS connection, tolerating an already-torn-down transport.

    During an abnormal shutdown the server can drop the connection first (e.g.
    factory-nats restarting mid-converge → ``nats: unexpected EOF``), tearing
    down the asyncio transport while the client is not yet ``CLOSED``. A bare
    ``nc.close()`` in that window can raise (e.g. ``TypeError`` from the
    transport teardown / ``wait_closed()`` on a dead transport), masking the
    real exit path. Guard on ``is_closed`` and swallow any residual teardown
    error — close is best-effort on the way out. (#1989)
    """
    if nc.is_closed:
        return
    try:
        await nc.close()
    except (nats.errors.Error, OSError, TypeError):
        log.warning(
            "adapter_standalone: nc.close() failed during shutdown", exc_info=True
        )


async def _bootstrap_adapter_standalone(  # noqa: PLR0915, C901 — DEBT:migration-sequence-bootstrap
    raw_config: dict,
    platform: str,
    *,
    _stop: asyncio.Event | None = None,
) -> None:
    """Bootstrap a standalone adapter process connected to NATS.

    Args:
        raw_config: Parsed config dict (factory config.toml content).
        platform: "telegram", "discord", or "web".
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
        from factory.bootstrap.fleet_reporter import start_fleet_reporter

        await start_fleet_reporter(nc)
        log.info(
            "adapter_standalone: connected to NATS at %s",
            scrub_nats_url(nats_url),
        )
    except (nats.errors.Error, OSError, asyncio.TimeoutError, ssl.SSLError) as exc:
        # Broadened to include asyncio.TimeoutError (connect hangs) and
        # ssl.SSLError (TLS handshake failure) — both surfaced during NATS
        # connection setup and must be treated as fatal startup errors (#8).
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

            await bootstrap_discord_standalone(
                nc,
                raw_config,
                config_bundle,
                platform_enum,
                _stop=_stop,
            )
        elif platform == "web":
            from factory.bootstrap.wiring.standalone_web import (
                bootstrap_web_standalone,
            )

            await bootstrap_web_standalone(
                nc,
                raw_config,
                config_bundle,
                platform_enum,
                _stop=_stop,
            )
        else:
            sys.exit(f"Unknown platform: {platform!r}")
    finally:
        await _close_quietly(nc)
