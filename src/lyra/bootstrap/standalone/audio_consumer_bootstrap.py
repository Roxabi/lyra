"""Bootstrap helper for the durable JetStream outbound-audio consumer.

Called once per (platform, bot_id) pair from bootstrap_telegram_standalone /
bootstrap_discord_standalone (via standalone_telegram.py / standalone_discord.py)
after the adapter's astart() and typing-listener start() succeed.

Ordering contract (post-S3 / ADR-079 sole-provisioner):
    # Stream LYRA_OUTBOUND_AUDIO and KV lyra_outbound_audio_sent are provisioned
    # by the hub before announce_hub_ready (ADR-079 sole-provisioner). Adapters
    # are bind-only: js.key_value() binds the existing bucket; ensure_consumer()
    # creates the per-bot durable consumer. Do NOT call ensure_stream/ensure_kv here.
    kv = await js.key_value(KV_BUCKET)   -- bind-only; hub already provisioned
    await ensure_consumer(...)
    consumer = JetStreamAudioConsumer(...)
    await consumer.start()
    # ... running ...
    await consumer.stop()        -- called in _close_tg_wired / _close_dc_wired

On any failure, returns NullAudioConsumer() instead of raising (ADR-079 §c).
Teardown calls .stop() unconditionally on both real and null consumer.

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
from lyra.adapters.nats.null_audio_consumer import NullAudioConsumer
from lyra.infrastructure.outbound_audio.stream_setup import (
    KV_BUCKET,
    ensure_consumer,
)

if TYPE_CHECKING:
    from nats.js.client import JetStreamContext

log = logging.getLogger(__name__)


async def start_audio_consumer(
    js: "JetStreamContext",
    platform: str,
    bot_id: str,
    adapter: object,
) -> JetStreamAudioConsumer | NullAudioConsumer:
    """Bind KV + create durable consumer, then start a JetStreamAudioConsumer.

    The hub provisions stream LYRA_OUTBOUND_AUDIO and KV lyra_outbound_audio_sent
    before announce_hub_ready (ADR-079 sole-provisioner). This function is
    bind-only for the KV (js.key_value) and creates the per-bot durable consumer
    via ensure_consumer. Do NOT call ensure_stream/ensure_kv here.

    On any failure, logs at ERROR level and returns a NullAudioConsumer sentinel
    instead of raising.  The adapter boots and serves text fully; audio messages
    accumulate on LYRA_OUTBOUND_AUDIO (JetStream buffers up to MaxAge=24h) and
    are delivered when the next adapter restart succeeds (ADR-079 §c).

    Args:
        js:       JetStreamContext bound to the live NATS connection.
        platform: Platform string ("telegram" or "discord").
        bot_id:   Bot identifier string (e.g. "main").
        adapter:  Platform adapter instance; must expose ``render_audio`` and
                  ``send`` bound methods matching AudioSendFn / TextSendFn.

    Returns:
        A started ``JetStreamAudioConsumer`` on success, or ``NullAudioConsumer``
        on failure.  The caller MUST call ``await consumer.stop()`` in teardown
        in either case — both types satisfy the async stop() contract.
    """
    try:
        # Hub provisions stream + KV before announce_hub_ready (ADR-079).
        # Adapters are bind-only: key_value() binds the existing bucket.
        kv = await js.key_value(KV_BUCKET)

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
    except Exception:
        log.exception(
            "audio_consumer_bootstrap: audio consumer failed to start"
            " (platform=%s bot_id=%s) — audio degraded, text unaffected",
            platform,
            bot_id,
        )
        return NullAudioConsumer()
