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


def _deprecated_env(old_var: str, new_var: str) -> str | None:
    val = os.environ.get(old_var)
    if val is not None:
        import warnings

        warnings.warn(
            f"{old_var} is deprecated; use {new_var} instead",
            DeprecationWarning,
            stacklevel=3,
        )
    return val


def init_nats_stt(nc: "NATS") -> "NatsSttClient":
    """Initialise NATS STT client with heartbeat-based worker discovery.

    Always creates the client; workers are discovered via heartbeats.
    The client is available only when workers are registered.
    """
    from lyra.nats.nats_stt_client import NatsSttClient

    model = (
        os.environ.get("LYRA_STT_MODEL")
        or _deprecated_env("STT_MODEL_SIZE", "LYRA_STT_MODEL")
        or "large-v3-turbo"
    )
    client = NatsSttClient(nc=nc, model=model)
    log.info("STT client created (model=%s) — availability via heartbeat", model)
    return client


def init_nats_tts(nc: "NATS") -> "NatsTtsClient":
    """Initialise NATS TTS client with heartbeat-based worker discovery.

    Always creates the client; workers are discovered via heartbeats.
    The client is available only when workers are registered.
    """
    from lyra.nats.nats_tts_client import NatsTtsClient

    client = NatsTtsClient(nc=nc)
    log.info("TTS client created — availability via heartbeat")
    return client


async def probe_voice_services(
    nc: "NATS",
    stt: object | None,
    tts: object | None,
) -> None:
    """Ping STT/TTS adapters at startup; log a warning if unreachable.

    Non-fatal: hub starts regardless. Per-request circuit breaker handles
    ongoing availability tracking.
    """
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
        except Exception as exc:
            log.warning(
                "%s probe failed unexpectedly: %s: %s",
                name,
                type(exc).__name__,
                exc,
            )
