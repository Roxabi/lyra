"""Daemon entrypoint for the social media NATS satellite adapter."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from factory.adapters.socialmedia.adapter import SocialMediaNatsAdapter
from factory.adapters.socialmedia.media import upload_blob_refs
from factory.adapters.socialmedia.postiz_client import PostizPublicApiClient
from roxabi_satellite.blobs import (
    BlobstoreConfigError,
    apply_factory_blobstore_env_aliases,
    get_blobstore,
)

log = logging.getLogger(__name__)


class DaemonConfigError(Exception):
    pass


def _load_api_key() -> str:
    path = os.environ.get("SOCIALMEDIA_PROVIDER_API_KEY_PATH", "").strip()
    if not path:
        raise DaemonConfigError("SOCIALMEDIA_PROVIDER_API_KEY_PATH is required")
    key = Path(path).read_text(encoding="utf-8").strip()
    if not key:
        raise DaemonConfigError(f"empty API key at {path}")
    return key


def _load_base_url() -> str:
    url = os.environ.get("SOCIALMEDIA_PROVIDER_BASE_URL", "").strip()
    if not url:
        raise DaemonConfigError("SOCIALMEDIA_PROVIDER_BASE_URL is required")
    return url


async def _run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")  # noqa: E501
    nats_url = os.environ.get("NATS_URL", "nats://factory-nats:4222")
    base_url = _load_base_url()
    api_key = _load_api_key()

    postiz = PostizPublicApiClient(base_url, api_key)
    blob_store = None
    apply_factory_blobstore_env_aliases()
    try:
        blob_store = get_blobstore()
        log.info("blobstore enabled for media uploads")
    except BlobstoreConfigError:
        log.warning("blobstore not configured — media uploads disabled")

    async def upload_media(blobs):
        if not blobs:
            return []
        if blob_store is None:
            raise RuntimeError("media requested but blobstore is not configured")
        return await upload_blob_refs(
            blob_store=blob_store, postiz=postiz, blobs=blobs
        )

    from roxabi_obs import cancel_fleet_reporter, start_fleet_reporter

    from roxabi_nats import nats_connect

    adapter = SocialMediaNatsAdapter(
        postiz,
        provider_base_url=base_url,
        upload_media=upload_media,
    )
    fleet_reporter_task = None
    try:
        log.info("socialmedia-adapter starting (provider=%s)", base_url)
        nc = await nats_connect(nats_url, identity_name="socialmedia-adapter")
        fleet_reporter_task = await start_fleet_reporter(nc)
        await adapter.run_embedded(nc)
    finally:
        await cancel_fleet_reporter(fleet_reporter_task)
        await postiz.aclose()
        if blob_store is not None:
            await blob_store.__aexit__(None, None, None)


def main() -> None:
    try:
        asyncio.run(_run())
    except DaemonConfigError as exc:
        log.error("%s", exc)
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()