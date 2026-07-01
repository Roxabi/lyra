"""Pipeline panel SSE — hub read-model fanout (#1760)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING, Any

from factory.dashboard.e2e import e2e_enabled, stub_pipeline

if TYPE_CHECKING:
    from factory.dashboard.hub_client import DashboardHubClient

log = logging.getLogger(__name__)

PIPELINE_STREAM_ID = "pipeline"
PIPELINE_SSE_POLL_S = 5.0


def _snapshot_digest(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def _load_pipeline_snapshot(
    hub: DashboardHubClient,
) -> dict[str, Any]:
    if e2e_enabled():
        return stub_pipeline().model_dump()
    return (await hub.pipeline_list()).model_dump()


async def pipeline_sse_events(
    hub: DashboardHubClient,
    *,
    is_connected: Callable[[], Awaitable[bool]] | None = None,
) -> AsyncIterator[str]:
    """Yield SSE frames from hub pipeline RPC; ping when unchanged."""
    last_digest: str | None = None
    while is_connected is None or await is_connected():
        try:
            snapshot = await _load_pipeline_snapshot(hub)
            digest = _snapshot_digest(snapshot)
            if digest != last_digest:
                last_digest = digest
                frame = {"type": "snapshot", "runs": snapshot.get("runs", [])}
                yield "data: " + json.dumps(frame) + "\n\n"
            else:
                yield "data: " + json.dumps({"type": "ping"}) + "\n\n"
        except Exception as exc:  # noqa: BLE001 — surface hub errors on stream
            log.warning("pipeline_sse: snapshot failed", exc_info=True)
            yield (
                "data: "
                + json.dumps({"type": "error", "message": str(exc)})
                + "\n\n"
            )
        await _interruptible_sleep(PIPELINE_SSE_POLL_S, is_connected=is_connected)


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