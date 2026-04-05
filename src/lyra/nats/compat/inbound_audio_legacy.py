"""Compat shim: legacy InboundAudio NATS subject → unified InboundMessage.

Phase 1 of issue #534.  Adapters still publishing on the deprecated
``lyra.inbound.audio.*`` subject tree are bridged here into the unified
``InboundMessage(modality="voice")`` inbound path.

Delete this entire module (and the ``nats/compat/`` package) in Phase 2 of
#534, once all adapters have migrated to the single inbound subject.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any

from lyra.core.audio_payload import AudioPayload
from lyra.core.message import (
    SCHEMA_VERSION_INBOUND_AUDIO,
    InboundAudio,
    InboundMessage,
)
from lyra.nats._serialize import deserialize_dict
from lyra.nats._version_check import check_schema_version

log = logging.getLogger(__name__)

# NATS subject for all legacy audio messages produced by adapters on old code.
_LEGACY_SUBJECT = "lyra.inbound.audio.>"

# Queue group — must match what NatsBus[InboundMessage] uses for the new subject
# so that multiple hub processes share the load rather than fan-out duplicate
# messages.  Source of truth: src/lyra/nats/queue_groups.py.
_QUEUE_GROUP = "hub-inbound"

_B64_PREFIX = "b64:"


def _normalize_bytes_field(value: Any) -> Any:
    """Convert list-of-ints wire format to b64-prefixed string for deserialize_dict.

    Legacy adapters (and the test helpers) may serialize ``bytes`` fields as a
    JSON array of integers rather than the canonical ``b64:<base64>`` string
    produced by ``_serialize.serialize()``.  This helper normalises the value
    so that ``deserialize_dict`` can reconstruct the ``bytes`` field correctly.
    """
    if isinstance(value, list):
        try:
            raw = bytes(value)
            return _B64_PREFIX + base64.b64encode(raw).decode("ascii")
        except Exception:
            return value
    return value


class InboundAudioLegacyHandler:
    """Raw NATS subscription handler that bridges legacy audio subjects.

    Subscribes to ``lyra.inbound.audio.>`` with queue group ``hub-inbound``,
    deserializes each message as ``InboundAudio``, converts it to
    ``InboundMessage(modality="voice", audio=AudioPayload(...))`` and injects
    it into *inbound_bus* via the public ``Bus.inject()`` API.

    Parameters
    ----------
    inbound_bus:
        The hub's unified inbound bus (``LocalBus[InboundMessage]`` in
        production, a ``MagicMock`` in tests).  Must expose ``inject(item)``.
    nats_client:
        Optional live NATS client.  Required for ``start()`` to subscribe;
        omit (or pass ``None``) in unit tests that call ``_on_message``
        directly.
    """

    def __init__(self, inbound_bus: Any, nats_client: Any = None) -> None:
        self._inbound_bus = inbound_bus
        self._nats_client = nats_client
        # Public counters — tests assert on these by name.
        self.decode_errors_total: int = 0
        self.converted_total: int = 0

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Subscribe to the legacy audio subject tree.

        Must be called after the NATS client is connected.  No-op if
        ``nats_client`` was not provided (test mode).
        """
        if self._nats_client is None:
            log.debug(
                "InboundAudioLegacyHandler.start() called without a NATS client"
                " — subscription skipped (test mode)"
            )
            return
        await self._nats_client.subscribe(
            subject=_LEGACY_SUBJECT,
            queue=_QUEUE_GROUP,
            cb=self._on_message,
        )
        log.info(
            "InboundAudioLegacyHandler subscribed to subject=%s queue=%s",
            _LEGACY_SUBJECT,
            _QUEUE_GROUP,
        )

    # ------------------------------------------------------------------
    # Per-message handler
    # ------------------------------------------------------------------

    async def _on_message(self, nats_msg: Any) -> None:
        """Handle one NATS message from the legacy audio subject tree."""
        try:
            await self._handle(nats_msg)
        except Exception:
            log.exception(
                "InboundAudioLegacyHandler: unexpected error — dropping message"
            )
            self.decode_errors_total += 1

    async def _handle(self, nats_msg: Any) -> None:
        # Step 1 — parse JSON.
        try:
            payload: dict[str, Any] = json.loads(nats_msg.data.decode("utf-8"))
        except Exception:
            log.warning(
                "InboundAudioLegacyHandler: failed to parse JSON — dropping message"
            )
            self.decode_errors_total += 1
            return

        # Step 2 — schema version check (parity with NatsBus._make_handler).
        if not check_schema_version(
            payload,
            envelope_name="InboundAudio",
            expected=SCHEMA_VERSION_INBOUND_AUDIO,
            subject=_LEGACY_SUBJECT,
        ):
            log.warning(
                "InboundAudioLegacyHandler: schema version mismatch — dropping message"
            )
            self.decode_errors_total += 1
            return

        # Step 3 — normalize bytes fields that may be encoded as list-of-ints
        # (legacy adapter wire format) rather than the canonical b64 string.
        if "audio_bytes" in payload:
            payload = dict(payload)
            payload["audio_bytes"] = _normalize_bytes_field(payload["audio_bytes"])

        # Step 4 — deserialize InboundAudio.
        try:
            legacy: InboundAudio = deserialize_dict(payload, InboundAudio)
        except Exception:
            log.warning(
                "InboundAudioLegacyHandler: failed to deserialize InboundAudio"
                " — dropping message"
            )
            self.decode_errors_total += 1
            return

        # Step 5 — convert to unified InboundMessage.
        converted = InboundAudioLegacyHandler._convert_legacy(legacy)

        # Step 6 — inject via public Bus API.
        self._inbound_bus.inject(converted)
        self.converted_total += 1
        log.info(
            "InboundAudioLegacyHandler: converted legacy audio → InboundMessage"
            " platform=%s bot_id=%s user_id=%s",
            legacy.platform,
            legacy.bot_id,
            legacy.user_id,
        )

    # ------------------------------------------------------------------
    # Conversion helper
    # ------------------------------------------------------------------

    @staticmethod
    def _convert_legacy(legacy: InboundAudio) -> InboundMessage:
        """Convert a legacy ``InboundAudio`` envelope to a unified ``InboundMessage``.

        All platform identity fields are carried over unchanged.  The audio
        data is nested inside an ``AudioPayload``; ``text`` / ``text_raw`` are
        empty strings because transcription has not yet run.
        ``waveform_b64`` is not present on ``InboundAudio`` — passed as
        ``None``.
        """
        audio_payload = AudioPayload(
            audio_bytes=legacy.audio_bytes,
            mime_type=legacy.mime_type,
            duration_ms=legacy.duration_ms,
            file_id=legacy.file_id,
            waveform_b64=None,
        )
        return InboundMessage(
            id=legacy.id,
            platform=legacy.platform,
            bot_id=legacy.bot_id,
            scope_id=legacy.scope_id,
            user_id=legacy.user_id,
            user_name=legacy.user_name,
            is_mention=legacy.is_mention,
            text="",
            text_raw="",
            trust_level=legacy.trust_level,
            trust=legacy.trust,
            timestamp=legacy.timestamp,
            platform_meta=legacy.platform_meta,
            routing=legacy.routing,
            modality="voice",
            audio=audio_payload,
        )
