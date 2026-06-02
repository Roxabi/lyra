"""Stream keepalive helpers for NatsChannelProxy.

Extracted from nats_channel_proxy.py (#1589) to shrink the class below
300 lines.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

import nats.errors
from nats.aio.client import Client as NATS

log = logging.getLogger(__name__)

KEEPALIVE_INTERVAL_S = 30.0
KEEPALIVE_EVENT_TYPE = "stream_keepalive"


async def _run_keepalive_loop(
    nc: NATS,
    subject: str,
    stream_id: str,
    seq_box: list[int],
    last_publish_box: list[float],
) -> None:
    """Publish stream_keepalive sentinels during idle periods (#687).

    ``seq_box`` and ``last_publish_box`` are single-element lists used as
    mutable references shared with the caller's publish loop.  Keepalive only
    fires when the elapsed time since the last real publish exceeds
    ``KEEPALIVE_INTERVAL_S``.
    """
    while True:
        await asyncio.sleep(KEEPALIVE_INTERVAL_S)
        if time.monotonic() - last_publish_box[0] >= KEEPALIVE_INTERVAL_S:
            ka_seq = seq_box[0]
            seq_box[0] += 1
            chunk = {
                "stream_id": stream_id,
                "seq": ka_seq,
                "event_type": KEEPALIVE_EVENT_TYPE,
                "payload": {},
                "done": False,
            }
            try:
                await nc.publish(
                    subject,
                    json.dumps(chunk, ensure_ascii=False).encode("utf-8"),
                )
                log.debug("keepalive published stream_id=%s seq=%d", stream_id, ka_seq)
            except nats.errors.Error:
                log.warning(
                    "NatsChannelProxy: failed to publish keepalive for stream_id=%r",
                    stream_id,
                )
