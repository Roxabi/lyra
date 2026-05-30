"""Voice overlay helpers — 3-layer DI for TTS, STT, Image.

Also hosts ``init_blobstore()`` — the composition-root factory for the
BlobStorePort (same infra-factory pattern as ``init_nats_*``).
"""

from __future__ import annotations

import logging
import os
import warnings
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

    from lyra.core.ports.blobstore import BlobStorePort
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
    from lyra.nats.worker_registry import WorkerRegistry
    from lyra.transport.nats_request_response import NatsTransport
    from lyra.transport.worker_pool_client import WorkerPoolClient
    from roxabi_contracts.voice import SUBJECTS, validate_worker_id

    transport = NatsTransport(nc)
    pool = WorkerPoolClient(
        transport,
        registry=WorkerRegistry(),
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
    from lyra.nats.worker_registry import WorkerRegistry
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
        registry=WorkerRegistry(),
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
    from lyra.nats.worker_registry import WorkerRegistry
    from lyra.transport.nats_request_response import NatsTransport
    from lyra.transport.worker_pool_client import WorkerPoolClient
    from roxabi_contracts.image import SUBJECTS, validate_worker_id

    transport = NatsTransport(nc)
    pool = WorkerPoolClient(
        transport,
        registry=WorkerRegistry(),
        hb_subject=SUBJECTS.image_heartbeat,
        validate_worker_id=validate_worker_id,
        name="image",
    )
    log.info("Image client created (3-layer) — availability via heartbeat")
    return NatsImageClient(pool, ImageCodec(), nc=nc)


def init_blobstore() -> "BlobStorePort | None":
    """Build and return an ``HttpBlobStoreAdapter`` satisfying ``BlobStorePort``.

    Reads ``LYRA_BLOBSTORE_URL`` and ``LYRA_BLOBSTORE_TOKEN_PATH`` once at
    construction time (restart-not-HUP semantics — token is never re-read
    without a process restart).

    Returns ``None`` when the token file is absent — the blob store is
    considered unconfigured and audio attachment upload is disabled until the
    file is present and the process is restarted.  Only ``FileNotFoundError``
    is suppressed; permission errors and other ``OSError`` subclasses propagate
    as real misconfiguration.  An empty token file raises ``OSError`` — an
    empty token would yield ``Bearer `` and cause silent 401s at first use.
    """
    import ipaddress
    import os
    from pathlib import Path
    from urllib.parse import urlsplit

    from lyra.infrastructure.blobstore_adapter import HttpBlobStoreAdapter
    from roxabi_blobs import HttpBlobStore

    base_url = os.environ.get("LYRA_BLOBSTORE_URL", "http://localhost:8449")

    def _is_loopback(h: str | None) -> bool:
        if h is None:
            return False
        if h == "localhost":
            return True
        try:
            return ipaddress.ip_address(h).is_loopback  # 127.0.0.0/8, ::1
        except ValueError:
            return False

    _parts = urlsplit(base_url)
    if _parts.scheme == "http" and not _is_loopback(_parts.hostname):
        log.warning(
            "LYRA_BLOBSTORE_URL %s is non-loopback http — bearer token sent in "
            "cleartext (mitigated by Tailnet WireGuard); prefer https",
            base_url,
        )

    token_path = os.environ.get(
        "LYRA_BLOBSTORE_TOKEN_PATH",
        str(Path.home() / ".lyra" / "blobstore.tok"),
    )
    try:
        token = Path(token_path).read_text().strip()
    except FileNotFoundError:
        log.warning(
            "BlobStore token not found at %s — blob_store unavailable "
            "(audio attachments disabled until configured)",
            token_path,
        )
        return None
    if not token:
        raise OSError(f"BlobStore token file at {token_path!r} is empty")
    http_store = HttpBlobStore(base_url, token)
    log.info("BlobStore client created — url=%s", base_url)
    return HttpBlobStoreAdapter(http_store)


async def probe_voice_services(
    nc: "NATS",
    stt: object | None,
    tts: object | None,
) -> None:
    """Ping STT/TTS adapters at startup; log a warning if unreachable."""
    from nats.errors import NoRespondersError

    from roxabi_contracts.voice import SUBJECTS

    checks = [
        ("STT", SUBJECTS.stt_request, stt),
        ("TTS", SUBJECTS.tts_request, tts),
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
