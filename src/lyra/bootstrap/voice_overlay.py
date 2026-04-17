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


def init_nats_stt(nc: "NATS") -> "NatsSttClient | None":
    """Initialise NATS STT client if LYRA_STT_ENABLED=1."""
    if os.environ.get("LYRA_STT_ENABLED", "0") != "1":
        if os.environ.get("STT_MODEL_SIZE") and not os.environ.get("LYRA_STT_ENABLED"):
            log.warning(
                "STT_MODEL_SIZE is set but LYRA_STT_ENABLED=1 is absent"
                " — STT is disabled; add LYRA_STT_ENABLED=1 to enable"
            )
        return None
    from lyra.nats.nats_stt_client import NatsSttClient

    model = (
        os.environ.get("LYRA_STT_MODEL")
        or _deprecated_env("STT_MODEL_SIZE", "LYRA_STT_MODEL")
        or "large-v3-turbo"
    )
    client = NatsSttClient(nc=nc, model=model)
    log.info("STT enabled via NATS (model=%s)", model)
    return client


def init_nats_tts(nc: "NATS") -> "NatsTtsClient | None":
    """Initialise NATS TTS client if LYRA_TTS_ENABLED=1 (independent of STT)."""
    if os.environ.get("LYRA_TTS_ENABLED", "0") != "1":
        return None
    from lyra.nats.nats_tts_client import NatsTtsClient

    client = NatsTtsClient(nc=nc)
    log.info("TTS enabled via NATS")
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
