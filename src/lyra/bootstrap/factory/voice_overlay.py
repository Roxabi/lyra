"""Voice overlay helpers — 3-layer DI for TTS, STT, Image."""

from __future__ import annotations

import logging
import os
import warnings
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

    from lyra.nats.nats_image_client import NatsImageClient
    from lyra.nats.nats_stt_client import NatsSttClient
    from lyra.nats.nats_tts_client import NatsTtsClient

log = logging.getLogger(__name__)


def _deprecated_env(old_var: str, new_var: str) -> str | None:
    val = os.environ.get(old_var)
    if val is not None:
        warnings.warn(
            f"{old_var} is deprecated; use {new_var} instead",
            DeprecationWarning,
            stacklevel=3,
        )
    return val


def init_nats_tts(nc: "NATS") -> "NatsTtsClient":
    """Create NatsTtsClient (3-layer). Call ``await client.start()`` to activate hb."""
    from lyra.nats.nats_tts_client import NatsTtsClient
    from lyra.nats.nats_tts_codec import TtsCodec
    from lyra.transport.nats_request_response import NatsTransport
    from lyra.transport.worker_pool_client import WorkerPoolClient
    from roxabi_contracts.voice import SUBJECTS, validate_worker_id

    transport = NatsTransport(nc)
    pool = WorkerPoolClient(
        transport,
        hb_subject=SUBJECTS.tts_heartbeat,
        validate_worker_id=validate_worker_id,
        name="tts",
    )
    log.info("TTS client created (3-layer) — availability via heartbeat")
    return NatsTtsClient(pool, TtsCodec(), nc=nc)


def init_nats_stt(nc: "NATS") -> "NatsSttClient":
    """Create NatsSttClient (3-layer). Call ``await client.start()`` to activate hb."""
    from lyra.nats.nats_stt_client import NatsSttClient
    from lyra.nats.nats_stt_codec import SttCodec
    from lyra.transport.nats_request_response import NatsTransport
    from lyra.transport.worker_pool_client import WorkerPoolClient
    from roxabi_contracts.voice import SUBJECTS, validate_worker_id

    model = (
        os.environ.get("LYRA_STT_MODEL")
        or _deprecated_env("STT_MODEL_SIZE", "LYRA_STT_MODEL")
        or "large-v3-turbo"
    )
    transport = NatsTransport(nc)
    pool = WorkerPoolClient(
        transport,
        hb_subject=SUBJECTS.stt_heartbeat,
        validate_worker_id=validate_worker_id,
        name="stt",
    )
    log.info(
        "STT client created (3-layer, model=%s) — availability via heartbeat", model
    )
    return NatsSttClient(pool, SttCodec(), model=model, nc=nc)


def init_nats_image(nc: "NATS") -> "NatsImageClient":
    """Create NatsImageClient (3-layer). Call ``client.start()`` to start hb."""
    from lyra.nats.nats_image_client import NatsImageClient
    from lyra.nats.nats_image_codec import ImageCodec
    from lyra.transport.nats_request_response import NatsTransport
    from lyra.transport.worker_pool_client import WorkerPoolClient
    from roxabi_contracts.image import SUBJECTS, validate_worker_id

    transport = NatsTransport(nc)
    pool = WorkerPoolClient(
        transport,
        hb_subject=SUBJECTS.image_heartbeat,
        validate_worker_id=validate_worker_id,
        name="image",
    )
    log.info("Image client created (3-layer) — availability via heartbeat")
    return NatsImageClient(pool, ImageCodec(), nc=nc)


async def probe_voice_services(
    nc: "NATS",
    stt: object | None,
    tts: object | None,
) -> None:
    """Ping STT/TTS adapters at startup; log a warning if unreachable."""
    from nats.errors import NoRespondersError

    checks = [
        ("STT", "lyra.voice.stt.request", stt),
        ("TTS", "lyra.voice.tts.request", tts),
    ]
    for name, subject, client in checks:
        if client is None:
            continue
        try:
            await nc.request(subject, b'{"ping":true}', timeout=1.0)
        except (NoRespondersError, TimeoutError):
            log.warning(
                "%s adapter not reachable at boot — will retry per-request", name
            )
        except Exception as exc:  # noqa: BLE001 — DEBT:boundary-broad-catch
            log.warning(
                "%s probe failed unexpectedly: %s: %s",
                name,
                type(exc).__name__,
                exc,
            )
