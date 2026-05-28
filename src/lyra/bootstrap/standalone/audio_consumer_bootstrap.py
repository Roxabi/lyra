"""Bootstrap helper for the durable JetStream outbound-audio consumer.

Called once per (platform, bot_id) pair from bootstrap_telegram_standalone /
bootstrap_discord_standalone (via standalone_telegram.py / standalone_discord.py)
after the adapter's astart() and typing-listener start() succeed.

Ordering contract (mirrors turn_writer_standalone.py):
    js = nc.jetstream()          -- called once per bootstrap_*_standalone
    await ensure_stream(js)
    kv = await ensure_kv(js)     -- idempotent; KvSentSet wraps the handle
    await ensure_consumer(...)
    consumer = JetStreamAudioConsumer(...)
    await consumer.start()
    # ... running ...
    await consumer.stop()        -- called in _close_tg_wired / _close_dc_wired

Consumer is per-bot (not per-platform): durable and filter_subject are scoped
to (platform, bot_id) so each bot's consumer is bound to its own adapter send
paths and receives only its own audio messages.

    durable        = "outbound-audio-{platform}-{bot_id}"
    filter_subject = "lyra.outbound.audio.{platform}.{bot_id}"
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from lyra.adapters.nats.jetstream_audio_consumer import JetStreamAudioConsumer
from lyra.adapters.nats.jetstream_audio_dedup import KvSentSet
from lyra.infrastructure.outbound_audio.stream_setup import (
    ensure_consumer,
    ensure_kv,
    ensure_stream,
)

if TYPE_CHECKING:
    from nats.js.client import JetStreamContext

log = logging.getLogger(__name__)


async def start_audio_consumer(
    js: "JetStreamContext",
    platform: str,
    bot_id: str,
    adapter: object,
) -> JetStreamAudioConsumer:
    """Provision stream/KV/consumer then construct and start a JetStreamAudioConsumer.

    Args:
        js:       JetStreamContext bound to the live NATS connection.
        platform: Platform string ("telegram" or "discord").
        bot_id:   Bot identifier string (e.g. "main").
        adapter:  Platform adapter instance; must expose ``render_audio`` and
                  ``send`` bound methods matching AudioSendFn / TextSendFn.

    Returns:
        A started ``JetStreamAudioConsumer``.  The caller is responsible for
        calling ``await consumer.stop()`` in teardown.
    """
    await ensure_stream(js)
    kv = await ensure_kv(js)

    durable = f"outbound-audio-{platform}-{bot_id}"
    filter_subject = f"lyra.outbound.audio.{platform}.{bot_id}"

    await ensure_consumer(js, durable=durable, filter_subject=filter_subject)

    consumer = JetStreamAudioConsumer(
        js,
        durable=durable,
        filter_subject=filter_subject,
        send_audio=adapter.render_audio,  # type: ignore[arg-type]
        send_text=adapter.send,  # type: ignore[arg-type]
        dedup=KvSentSet(kv),
    )
    await consumer.start()

    log.info(
        "audio_consumer_bootstrap: consumer started"
        " (platform=%s bot_id=%s durable=%s filter=%s)",
        platform,
        bot_id,
        durable,
        filter_subject,
    )
    return consumer
