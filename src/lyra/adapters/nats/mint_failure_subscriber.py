"""MintFailureSubscriber — NATS subscriber for lyra.gh.mint_failure.> events.

Subscribes to GitHub App token mint failures and forwards Telegram alerts to
the configured ops chat via the existing NATS outbound path.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import ValidationError

from lyra.core.auth.trust import TrustLevel
from lyra.core.messaging.message import (
    InboundMessage,
    OutboundMessage,
    Platform,
    RoutingContext,
    TelegramMeta,
)
from lyra.nats.type_registry import TYPE_REGISTRY_RESOLVER
from roxabi_contracts.gh.models import MintFailureEvent
from roxabi_nats._serialize import serialize

log = logging.getLogger(__name__)

_SUBSCRIBE_SUBJECT = "lyra.gh.mint_failure.>"


class MintFailureSubscriber:
    """Subscribes to lyra.gh.mint_failure.> and forwards alerts to ops Telegram chat."""

    def __init__(
        self,
        nc: Any,  # nats.aio.client.Client — Any to avoid hard import at module level
        *,
        ops_telegram_bot_id: str,
        ops_telegram_chat_id: int,
    ) -> None:
        self._nc = nc
        self._ops_telegram_bot_id = ops_telegram_bot_id
        self._ops_telegram_chat_id = ops_telegram_chat_id
        self._subject = f"lyra.outbound.{Platform.TELEGRAM.value}.{ops_telegram_bot_id}"
        self._sub: Any = None  # nats.aio.subscription.Subscription | None

    async def start(self) -> None:
        """Subscribe to lyra.gh.mint_failure.> on the hub NATS connection."""
        self._sub = await self._nc.subscribe(_SUBSCRIBE_SUBJECT, cb=self._handle)
        log.info(
            "MintFailureSubscriber started — subject=%r ops_chat=%d bot=%r",
            _SUBSCRIBE_SUBJECT,
            self._ops_telegram_chat_id,
            self._ops_telegram_bot_id,
        )

    async def stop(self) -> None:
        """Unsubscribe from NATS."""
        if self._sub is not None:
            await self._sub.unsubscribe()
            self._sub = None
            log.info("MintFailureSubscriber stopped")

    async def _handle(self, msg: Any) -> None:
        """Parse MintFailureEvent and publish a Telegram alert via outbound subject."""
        try:
            event = MintFailureEvent.model_validate_json(msg.data)
        except (ValidationError, ValueError, UnicodeDecodeError) as exc:
            log.warning(
                "MintFailureSubscriber: failed to parse payload on subject=%r: %s",
                msg.subject,
                exc,
            )
            return

        alert_text = (
            f"\U0001f6a8 Lyra GH mint failure on {event.machine}\n"
            f"Reason: {event.reason}\n"
            f"Status: {event.http_status}\n"
            f"Retries: {event.retries}\n"
            f"Subject: {msg.subject}"
        )

        stream_id = str(uuid.uuid4())
        scope_id = f"chat:{self._ops_telegram_chat_id}"

        original_msg = InboundMessage(
            id=stream_id,
            platform=Platform.TELEGRAM.value,
            bot_id=self._ops_telegram_bot_id,
            scope_id=scope_id,
            user_id="system",
            user_name="system",
            is_mention=False,
            text="",
            text_raw="",
            trust_level=TrustLevel.TRUSTED,
            timestamp=datetime.now(timezone.utc),
            platform_meta=TelegramMeta(
                chat_id=self._ops_telegram_chat_id,
            ),
            routing=RoutingContext(
                platform=Platform.TELEGRAM.value,
                bot_id=self._ops_telegram_bot_id,
                scope_id=scope_id,
            ),
        )

        outbound = OutboundMessage.from_text(alert_text)

        resolver = TYPE_REGISTRY_RESOLVER
        envelope = {
            "type": "send",
            "stream_id": stream_id,
            "outbound": json.loads(
                serialize(outbound, resolver=resolver).decode("utf-8")
            ),
            "original_msg": json.loads(
                serialize(original_msg, resolver=resolver).decode("utf-8")
            ),
        }
        payload = json.dumps(envelope, ensure_ascii=False).encode("utf-8")

        try:
            await self._nc.publish(self._subject, payload)
        except Exception as exc:  # noqa: BLE001   — POLICY:boundary# NATS publish: exception type varies
            log.warning(
                "MintFailureSubscriber: failed to publish alert for machine=%r: %s",
                event.machine,
                exc,
            )
            return

        log.info(
            "MintFailureSubscriber: alert published for machine=%r reason=%r",
            event.machine,
            event.reason,
        )
