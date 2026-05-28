"""DeadLetterConsumer — JetStream DLQ subscriber → user notification."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from lyra.core.messaging.message import InboundMessage, OutboundMessage, Platform
from lyra.nats.queue_groups import adapter_outbound
from lyra.nats.type_registry import TYPE_REGISTRY_RESOLVER
from roxabi_nats._serialize import deserialize_dict
from roxabi_nats._validate import validate_nats_token

if TYPE_CHECKING:
    from nats.js.client import JetStreamContext

    from lyra.core.hub.hub_protocol import ChannelAdapter

log = logging.getLogger(__name__)


class DeadLetterConsumer:
    """Consumes dead-letter messages from JetStream and notifies the user."""

    def __init__(
        self,
        js: JetStreamContext,
        platform: Platform,
        bot_id: str,
        adapter: ChannelAdapter,
        *,
        subject: str | None = None,
    ) -> None:
        self._js = js
        self._platform = platform
        self._bot_id = bot_id
        validate_nats_token(bot_id, kind="bot_id")
        self._adapter = adapter
        self._subject = subject or f"lyra.outbound.dlq.{platform.value}.{bot_id}"
        self._sub: Any = None

    async def start(self) -> None:
        """Subscribe to the DLQ subject."""
        from nats.js.api import ConsumerConfig

        self._sub = await self._js.subscribe(
            self._subject,
            cb=self._handle,
            manual_ack=True,
            queue=adapter_outbound(self._platform.value, self._bot_id),
            config=ConsumerConfig(max_deliver=1),
        )

    async def stop(self) -> None:
        """Unsubscribe from the DLQ subject."""
        if self._sub is not None:
            await self._sub.unsubscribe()
            self._sub = None

    async def _handle(self, msg: Any) -> None:
        """Process a dead-letter message and notify the user."""
        try:
            await self._notify_user(msg)
            if hasattr(msg, "ack"):
                await msg.ack()
        except Exception:  # noqa: BLE001 — DEBT:boundary-broad-catch
            log.exception("DeadLetterConsumer: failed to process DLQ message")
            if hasattr(msg, "term"):
                await msg.term()
            elif hasattr(msg, "ack"):
                await msg.ack()

    async def _notify_user(self, msg: Any) -> None:
        """Extract original_msg from the DLQ envelope and notify the user."""
        data = json.loads(msg.data.decode("utf-8"))
        raw_original = data.get("original_msg")
        if raw_original is None:
            log.warning("DeadLetterConsumer: DLQ envelope missing original_msg")
            return

        try:
            original_msg = deserialize_dict(
                raw_original, InboundMessage, resolver=TYPE_REGISTRY_RESOLVER
            )
        except Exception:  # noqa: BLE001 — DEBT:boundary-broad-catch
            log.exception("DeadLetterConsumer: failed to deserialize original_msg")
            return

        send_failure_notification = getattr(
            self._adapter, "send_failure_notification", None
        )
        if send_failure_notification is not None:
            await send_failure_notification(original_msg, reason="delivery_failed")
        else:
            await self._adapter.send(
                original_msg,
                OutboundMessage.from_text(
                    "⚠️ Message delivery failed after retries. Please try again."
                ),
            )
