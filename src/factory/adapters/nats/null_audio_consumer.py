"""NullAudioConsumer — no-op sentinel returned by start_audio_consumer on degraded boot.

Satisfies the consumer interface (async stop()) so teardown calls .stop()
unconditionally without if-guards.  No platform-axis guard code needed — the
sentinel collapses N guard sites to zero (ADR-079 §c).
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class NullAudioConsumer:
    """No-op sentinel returned by start_audio_consumer on audio-degraded boot.

    Teardown in _close_tg_wired / _close_dc_wired calls consumer.stop()
    unconditionally; this sentinel makes that safe regardless of whether audio
    provisioning succeeded.  Audio messages accumulate on LYRA_OUTBOUND_AUDIO
    (buffered by JetStream up to MaxAge=24h) and are delivered when the next
    adapter restart succeeds.
    """

    async def stop(self) -> None:
        """No-op: nothing to cancel or unsubscribe."""
