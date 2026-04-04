"""Voice overlay helpers — NATS STT/TTS client initialisation."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

    from lyra.nats.nats_stt_client import NatsSttClient
    from lyra.nats.nats_tts_client import NatsTtsClient

log = logging.getLogger(__name__)


def init_nats_stt(nc: "NATS") -> "NatsSttClient | None":
    """Initialise NATS STT client if STT_MODEL_SIZE is set."""
    if not os.environ.get("STT_MODEL_SIZE"):
        return None
    from lyra.nats.nats_stt_client import NatsSttClient

    model = os.environ.get("STT_MODEL_SIZE", "large-v3-turbo")
    client = NatsSttClient(nc=nc, model=model)
    log.info("STT enabled via NATS (model=%s)", model)
    return client


def init_nats_tts(nc: "NATS", stt_client: object | None) -> "NatsTtsClient | None":
    """Initialise NATS TTS client if STT is enabled and voice responses are on."""
    voice_responses = os.environ.get("LYRA_VOICE_RESPONSES", "1") != "0"
    if stt_client is None or not voice_responses:
        return None
    from lyra.nats.nats_tts_client import NatsTtsClient

    client = NatsTtsClient(nc=nc)
    log.info("TTS voice responses enabled via NATS")
    return client
