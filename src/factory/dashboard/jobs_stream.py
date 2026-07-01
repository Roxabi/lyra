"""Jobs panel SSE — hub active-jobs registry fanout (#1772, #1800 v1).

Mirrors ``pipeline_stream`` — server-side snapshot poll of the hub's
active-jobs registry, pushed to the browser over one persistent SSE
connection (``EventSource``).  The browser no longer polls; it receives a
``snapshot`` frame only when the registry digest changes, else a ``ping``.

Server-side poll cadence (``JOBS_SSE_POLL_S``) is intentionally modest — a
future upgrade to ``factory.job.<id>.opened/.closed`` event fanout (Shape-D)
would make this sub-second, but that needs new ACL grants (see
``deploy/nats/acl-matrix.json``); the KV read path here stays zero-ACL.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING, Any

from factory.dashboard.e2e import e2e_enabled, stub_jobs_list

if TYPE_CHECKING:
    from factory.dashboard.hub_client import DashboardHubClient

log = logging.getLogger(__name__)

JOBS_STREAM_ID = "jobs"
JOBS_SSE_POLL_S = 2.0


def _snapshot_digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def _load_jobs_snapshot(hub: DashboardHubClient) -> dict[str, Any]:
    if e2e_enabled():
        return stub_jobs_list().model_dump()
    return (await hub.list_jobs()).model_dump()


async def jobs_sse_events(
    hub: DashboardHubClient,
    *,
    is_connected: Callable[[], Awaitable[bool]] | None = None,
) -> AsyncIterator[str]:
    """Yield SSE frames from hub active-jobs RPC; ping when unchanged."""
    last_digest: str | None = None
    while is_connected is None or await is_connected():
        try:
            snapshot = await _load_jobs_snapshot(hub)
            digest = _snapshot_digest(snapshot)
            if digest != last_digest:
                last_digest = digest
                frame = {"type": "snapshot", "jobs": snapshot.get("jobs", [])}
                yield "data: " + json.dumps(frame) + "\n\n"
            else:
                yield "data: " + json.dumps({"type": "ping"}) + "\n\n"
        except Exception as exc:  # noqa: BLE001 — surface hub errors on stream
            log.warning("jobs_sse: snapshot failed", exc_info=True)
            yield (
                "data: " + json.dumps({"type": "error", "message": str(exc)}) + "\n\n"
            )
        await _interruptible_sleep(JOBS_SSE_POLL_S, is_connected=is_connected)


async def _interruptible_sleep(
    seconds: float,
    *,
    is_connected: Callable[[], Awaitable[bool]] | None,
) -> None:
    elapsed = 0.0
    step = 0.25
    while elapsed < seconds:
        if is_connected is not None and not await is_connected():
            return
        wait = min(step, seconds - elapsed)
        await asyncio.sleep(wait)
        elapsed += wait
