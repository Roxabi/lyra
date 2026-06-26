"""BlobStore → Postiz media upload bridge."""

from __future__ import annotations

import mimetypes

from roxabi_blobs import HttpBlobStore
from roxabi_contracts.blob_ref import BlobRef

from factory.adapters.socialmedia.postiz_client import PostizPublicApiClient


def _filename_for_blob(blob: BlobRef) -> str:
    if blob.filename:
        return blob.filename
    ext = mimetypes.guess_extension(blob.mime) or ".bin"
    return f"upload{ext}"


async def upload_blob_refs(
    *,
    blob_store: HttpBlobStore,
    postiz: PostizPublicApiClient,
    blobs: list[BlobRef],
) -> list[dict]:
    media_items: list[dict] = []
    for blob in blobs:
        content = await blob_store.get(blob.store_key)
        uploaded = await postiz.upload_bytes(
            filename=_filename_for_blob(blob),
            content=content,
            content_type=blob.mime,
        )
        path = uploaded.get("path")
        media_id = uploaded.get("id")
        if not path or not media_id:
            raise ValueError("Postiz upload response missing id/path")
        media_items.append({"id": str(media_id), "path": str(path)})
    return media_items