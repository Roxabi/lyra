"""Factory glue for roxabi-obs fleet reporter."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from roxabi_obs import cancel_fleet_reporter as _cancel_obs_reporter
from roxabi_obs import start_fleet_reporter as _start_obs_reporter

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS


async def start_fleet_reporter(nc: NATS) -> asyncio.Task[None] | None:
    """Start in-process fleet reporter when CONTAINER_NAME is set."""
    return await _start_obs_reporter(nc)


async def cancel_fleet_reporter(task: asyncio.Task[None] | None) -> None:
    """Cancel fleet reporter task during shutdown."""
    await _cancel_obs_reporter(task)