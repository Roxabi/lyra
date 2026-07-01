"""Periodic ContainerReport publisher."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from pathlib import Path
from typing import TYPE_CHECKING

from roxabi_contracts.fleet import CONTAINER_REPORT
from roxabi_contracts.fleet.models import ContainerHealth, new_container_report

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

log = logging.getLogger(__name__)

_DEFAULT_INTERVAL_S = 30.0
_BUILD_INFO_PATH = Path("/app/.roxabi-build-info.json")


def _read_build_revision() -> str | None:
    env_rev = os.environ.get("IMAGE_REVISION", "").strip()
    if env_rev:
        return env_rev
    if _BUILD_INFO_PATH.is_file():
        try:
            data = json.loads(_BUILD_INFO_PATH.read_text(encoding="utf-8"))
            rev = data.get("revision") or data.get("git_sha")
            if isinstance(rev, str) and rev.strip():
                return rev.strip()
        except (OSError, json.JSONDecodeError, TypeError):
            log.debug("roxabi_obs: could not read build info", exc_info=True)
    return None


def _resolve_host() -> str:
    return (
        os.environ.get("FACTORY_MACHINE", "").strip()
        or os.environ.get("HOSTNAME", "").strip()
        or socket.gethostname()
    )


class FleetReporter:
    """Publish ContainerReport snapshots on a fixed interval."""

    def __init__(
        self,
        nc: NATS,
        *,
        interval_s: float = _DEFAULT_INTERVAL_S,
        container_name: str | None = None,
        image_ref: str | None = None,
        health: ContainerHealth = "healthy",
    ) -> None:
        self._nc = nc
        self._interval_s = interval_s
        env_name = os.environ.get("CONTAINER_NAME", "")
        self._container_name = (container_name or env_name).strip()
        self._image_ref = (image_ref or os.environ.get("IMAGE_REF", "")).strip()
        self._health: ContainerHealth = health
        self._task: asyncio.Task[None] | None = None

    async def run(self) -> None:
        if not self._container_name:
            log.warning("roxabi_obs: CONTAINER_NAME unset — fleet reporter disabled")
            return
        if not self._image_ref:
            log.warning(
                "roxabi_obs: IMAGE_REF unset for %s — using unknown",
                self._container_name,
            )
            self._image_ref = "unknown"
        while True:
            await self._publish_once()
            await asyncio.sleep(self._interval_s)

    async def _publish_once(self) -> None:
        report = new_container_report(
            host=_resolve_host(),
            container_name=self._container_name,
            image_ref=self._image_ref,
            image_revision=_read_build_revision(),
            health=self._health,
        )
        payload = report.model_dump_json().encode()
        try:
            await self._nc.publish(CONTAINER_REPORT, payload)
        except Exception:  # noqa: BLE001 — reporter must not crash the host process
            log.warning(
                "roxabi_obs: publish failed for %s",
                self._container_name,
                exc_info=True,
            )


async def start_fleet_reporter(nc: NATS) -> asyncio.Task[None] | None:
    """Start background fleet reporter when CONTAINER_NAME is configured."""
    if not os.environ.get("CONTAINER_NAME", "").strip():
        return None
    reporter = FleetReporter(nc)
    return asyncio.create_task(reporter.run(), name="fleet-reporter")


async def cancel_fleet_reporter(task: asyncio.Task[None] | None) -> None:
    """Stop a background fleet reporter task during process shutdown."""
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass