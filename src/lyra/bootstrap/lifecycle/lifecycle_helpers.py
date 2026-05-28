"""Shared lifecycle helpers for multibot and standalone Hub bootstrap."""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable, Sequence
from typing import Any, Protocol

log = logging.getLogger(__name__)


class _Stoppable(Protocol):
    async def stop(self) -> Any: ...


def setup_signal_handlers(stop: asyncio.Event) -> None:
    """Register SIGINT/SIGTERM handlers on the running event loop."""
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGINT, stop.set)
    loop.add_signal_handler(signal.SIGTERM, stop.set)


async def close_safely(label: str, *awaitables: Awaitable[Any]) -> None:
    """Close/stop resources concurrently, logging failures without re-raising.

    Accepts any Awaitable (coroutine, Task, Future). Skips BaseException
    subclasses that are not Exception (CancelledError, KeyboardInterrupt,
    SystemExit) — normal shutdown path. All resources are attempted regardless
    of individual failures.
    """
    if not awaitables:
        return
    results = await asyncio.gather(*awaitables, return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            log.exception("Close failed [%s]", label, exc_info=r)
        elif isinstance(r, BaseException):
            log.debug("Close cancelled [%s] — resource may not be fully closed", label)


async def teardown_buses(*buses: _Stoppable) -> None:
    """Stop all provided buses (Bus[T] instances)."""
    for bus in buses:
        await bus.stop()


async def teardown_dispatchers(dispatchers: Sequence[_Stoppable]) -> None:
    """Stop all outbound dispatchers."""
    for d in dispatchers:
        await d.stop()


def _on_nats_reconnect(freshness_drivers: list[Any]) -> Any:
    """Return a callback that clears worker freshness caches on NATS reconnect.

    The returned coroutine is suitable for the *reconnected_cb* parameter of
    ``nats_connect``.
    """

    async def callback() -> None:
        log.info("NATS reconnected — clearing worker freshness caches")
        for d in freshness_drivers:
            if hasattr(d, "_worker_freshness"):
                d._worker_freshness.clear()

    return callback


def _freshness_drivers() -> list[Any]:
    """Return a mutable list to hold drivers that carry worker freshness state.

    Pass the list to ``_on_nats_reconnect`` and extend it after drivers are
    built.
    """
    return []


async def _run_shutdown(  # noqa: PLR0913 — shutdown surface
    tasks: list[asyncio.Task[Any]],
    stop: asyncio.Event,
    mint_failure_sub: Any,
    hub: Any,
    readiness_sub: Any,
    dispatchers: list[Any],
    proxies: list[Any],
    pm: Any,
    cli_nats_driver: Any,
    nats_llm_client: Any,
) -> None:
    """Cancel tasks, wait for shutdown, and run teardown."""
    from lyra.bootstrap.factory.utils import watchdog
    from lyra.bootstrap.standalone.hub_standalone_helpers import shutdown_hub_runtime

    await watchdog(tasks, stop)
    log.info("Shutdown signal received — stopping...")
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    if mint_failure_sub is not None:
        await mint_failure_sub.stop()
    await shutdown_hub_runtime(
        hub,
        readiness_sub=readiness_sub,
        dispatchers=dispatchers,
        proxies=proxies,
        pm=pm,
        cli_nats_driver=cli_nats_driver,
        nats_llm_client=nats_llm_client,
    )
